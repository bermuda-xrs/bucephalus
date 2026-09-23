"""AgentContext — the object every module receives as `ctx`.

Deliberately explicit rather than clever: one place where the database,
the log, the anchor, the backends, the router, the tools, the mesh, the skill
store and the telemetry are wired together, so any invariant that spans two
subsystems has a single home.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from .assure.mesh import VerificationMesh
from .config import Config, load_config, load_dotenv
from .execute.tools import ToolRegistry, default_registry
from .instrument.telemetry import Telemetry, compute_cost, record_call
from .kernel.db import open_db
from .kernel.events import EventLog
from .kernel.integrity import Anchor
from .kernel.tasks import spend
from .kernel.types import ModelResponse, Taint
from .ledger.entries import LedgerEntry
from .models.registry import (build_backends, cache_reporting_backend_names,
                              cloud_backend_names)
from .router.cascade import Router
from .security.taint import TaintTracker
from .skills.store import SkillStore
from .userdata.store import UserStore


def _without(registry: ToolRegistry, *names: str) -> ToolRegistry:
    """A copy of `registry` minus these tools. Never mutates the original -
    `default_registry()` may be shared, and an ablation arm must not be able
    to strip a tool out of a context that did not ask for it."""
    out = ToolRegistry()
    for name in registry.names():
        if name not in names:
            out.register(registry.get(name))
    return out


class AgentContext:
    def __init__(
        self,
        config: Config,
        conn: sqlite3.Connection | None = None,
        backends: dict | None = None,
        tools: ToolRegistry | None = None,
    ):
        self.config = config
        self.conn = conn or open_db(config.resolve(config.db_path))
        self.log = EventLog(self.conn)
        self.anchor = Anchor(config.resolve(config.anchor_path))
        self.backends = backends if backends is not None else build_backends(config)
        self.tools = tools or default_registry()
        # G1's code-as-action arm. The tool leaves the REGISTRY, which is the
        # one place every route passes: `execute_action` looks a tool up
        # before anything else and refuses what is not there, `schemas()`
        # stops offering it, and `manual()` - rendered into the prefix - stops
        # describing it. `system_contract` drops the matching instruction.
        #
        # Deliberately NOT also a `tool_policy` denial. That check sits after
        # the registry lookup, so with the tool already gone it could never
        # fire: a check that exists, passes its test and cannot run is worse
        # than no check, because it reads as protection. See
        # config.MechanismsConfig.
        if not config.mechanisms.code_as_action:
            self.tools = _without(self.tools, "run_code")
        self.skills = SkillStore(self.conn, config.resolve(config.skills_path))
        self.userdata = UserStore(self.conn)
        # A1 tier 1. In the USER DATA dir, not the repo: memory is the user's,
        # it outlives any checkout, and a file the agent writes must never sit
        # where a `git status` makes it look like source.
        from .memory.store import MemoryStore
        from .paths import user_data_dir

        self.memory = MemoryStore(user_data_dir() / "memory")
        self._memory_frozen: dict[str, dict] = {}
        self.telemetry = Telemetry(self.conn, cloud_backend_names(config),
                                   cache_reporting_backend_names(config))
        self.mesh = VerificationMesh(self)
        self.router = Router(self)

        self.phase: str = "execute"  # "plan" makes every write tool unreachable
        self.executing_backend: str | None = None
        # An OVERRIDE SEAM, not the default. `tool_web_fetch` owns the default
        # path so it can record provenance — URL after redirects, fetch time,
        # content hash — which a plain text-returning callable cannot carry.
        #
        # This used to be pre-set to `default_fetcher`, which made the tool
        # take the override branch on every call and left the provenance path
        # unreachable: dead code that looked wired. Every caller in the tree
        # was already overriding it, so nothing lost a fetcher by this change.
        #
        # Our own fetcher either way, on purpose: content must enter through
        # the tool path so it lands in the ledger as UNTRUSTED and the gate
        # can see where it came from. See net/fetch.py for why a server-side
        # fetch tool is not a drop-in replacement.
        self.web_fetcher = None

        # B3. Per TASK, not per context, because a delegated child runs on
        # the same context as its parent and must not inherit its authority.
        self._tool_policy: dict[str, object] = {}
        self._origin: dict[str, str] = {}
        self._taint: dict[str, TaintTracker] = {}
        self._frozen_skills: dict[str, list[dict]] = {}

    # -- construction ------------------------------------------------------

    @classmethod
    def open(cls, config_path: str | Path = "config.yaml",
             profile: str | None = None) -> "AgentContext":
        load_dotenv(Path(config_path).parent / ".env")
        return cls(load_config(config_path, profile=profile))

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    # -- backends ----------------------------------------------------------

    def active_local_tier(self) -> str | None:
        tier = self.config.active_local_tier
        if not tier or tier not in self.backends:
            return None
        return tier

    def window_for(self, backend_name: str) -> int:
        b = self.backends.get(backend_name)
        return b.cfg.budgeted_window if b else 200000

    def record(
        self,
        task,
        resp: ModelResponse,
        purpose: str = "step",
        escalated: bool = False,
    ) -> float:
        """Every model call lands here. Bypass it and the numbers are wrong."""
        backend = self.backends[resp.backend]
        cost = compute_cost(resp.usage, backend.cost_vector)
        record_call(
            self.conn,
            task.id,
            resp.backend,
            purpose,
            resp.usage,
            cost,
            resp.wall_ms,
            escalated=escalated,
            thinking=backend.cfg.thinking,
        )
        # Money only. Seconds are charged by the LOOP against wall clock:
        # summing model time undercounts sandbox and verification time, and
        # made `budget_exhausted` contention-sensitive - two processes on one
        # CPU could manufacture that outcome on a task that would finish.
        spend(self.conn, self.log, task, usd=cost)
        return cost

    def call_model(self, task, backend, purpose: str, escalated: bool = False,
                   **kwargs) -> ModelResponse:
        """EVERY model call made for a task goes through here (invariant 12).

        D36. `calls` was written from a RETURNED response, so a call that
        stalled, crashed or was killed left no row - the slowest calls were
        exactly the ones missing from every figure drawn from `calls` - and
        five call sites (the deputy, the quarantined summarizer, the monitor,
        the self-consistency verifier, the generator) never wrote one at all.
        The row is now written BEFORE the call with null usage and completed
        on return; an abort records its time and why. A row that never
        completes is the record of a call that never returned.

        `task` is the Task - its budget is charged - or, for a call made on a
        task's behalf outside its run (the monitor), its id alone.
        """
        from .instrument.telemetry import abort_call, begin_call, complete_call
        from .models.stall import ModelStall

        b = self.backends[backend] if isinstance(backend, str) else backend
        task_id = task if isinstance(task, str) else task.id
        cid = begin_call(self.conn, task_id, b.name, purpose, escalated=escalated,
                         thinking=b.cfg.thinking)
        t0 = time.time()
        try:
            resp = b.complete(**kwargs)
        except BaseException as e:
            outcome = ("stalled" if isinstance(e, ModelStall) else
                       "interrupted" if isinstance(e, (KeyboardInterrupt, SystemExit))
                       else "error")
            abort_call(self.conn, cid, int((time.time() - t0) * 1000), outcome)
            raise
        cost = compute_cost(resp.usage, b.cost_vector)
        complete_call(self.conn, cid, resp.usage, cost, resp.wall_ms)
        if not isinstance(task, str):
            spend(self.conn, self.log, task, usd=cost)
        return resp

    # -- taint -------------------------------------------------------------

    def freeze_memory(self, task_id: str) -> dict:
        """Tier 1 as of the start of the CURRENT SESSION, held for its life.

        The same discipline as `freeze_skills`, for the same reason: anything
        the prefix is built from must not change while a session runs. Frozen
        per task rather than per context because a delegated child is its own
        task with its own start.

        HELD ON THE SESSION ROW since Phase 9. It was a dict entry, so a task
        resumed in another process re-read tier 1 as it stood NOW - writes
        made after the task started, which Phase 3 promised would stay
        invisible until the next boundary, appeared early. DR1's lifetime
        axis once more. A boundary (`rotate_session`) is now the only thing
        that replaces a snapshot.
        """
        if task_id not in self._memory_frozen:
            from .kernel import sessions

            open_row = sessions.current(self.conn, task_id)
            if open_row is not None:
                self._memory_frozen[task_id] = json.loads(open_row["memory_json"])
            else:
                snap = self._snapshot_memory(task_id)
                sessions.open_session(self.conn, self.log, task_id, snap)
                self._memory_frozen[task_id] = snap
        return self._memory_frozen[task_id]

    def _snapshot_memory(self, task_id: str) -> dict:
        """Tier 1 as it stands now, tamper-checked. Taken when a session opens."""
        from .memory.store import INTACT, check_fingerprint, tier1_snapshot

        isolated = bool(getattr(self.config, "isolate_retrieval", False))
        state = check_fingerprint(self.memory)
        if state != INTACT:
            # REFUSE TO LOAD, CONTINUE WITHOUT IT.
            #
            # Not a halt, and the distinction is the point. A chain break
            # destroys the ability to VERIFY, so anything concluded from the
            # run is unfounded and stopping is the only honest move. Memory
            # tampering destroys the trustworthiness of ONE INPUT, and the run
            # stays fully auditable without it. Dropping a corrupted input and
            # carrying on is what this system already does with a corrupted
            # observation.
            #
            # Continuing WITH the content is the attack succeeding, and halting
            # makes a text file anyone can edit into a denial of service.
            # Degraded-but-safe is the third option and it is the right one.
            self.log.append(task_id, "memory.tampered",
                            {"state": state,
                             "recorded_present": (self.memory.root / ".fingerprint").exists(),
                             "current": self.memory.fingerprint(),
                             "action": "tier 1 refused; task continues "
                                       "without persistent memory"})
            isolated = True
        return tier1_snapshot(self.memory, isolated=isolated)

    def rotate_session(self, task_id: str, seed_artifact: str | None,
                       detail: dict) -> dict:
        """A BOUNDARY: close this session, open a child with a FRESH snapshot.

        The only place tier 1 is re-read mid-task, and therefore the only place
        a deferred memory write - Phase 3's promise - becomes visible.
        """
        from .kernel import sessions

        self.freeze_memory(task_id)          # guarantees an open session
        parent = sessions.current(self.conn, task_id)
        sessions.close(self.conn, self.log, parent["id"], "boundary")
        snap = self._snapshot_memory(task_id)
        changed = snap != json.loads(parent["memory_json"])
        child = sessions.open_session(
            self.conn, self.log, task_id, snap, parent_id=parent["id"],
            reason="boundary", seed_artifact=seed_artifact,
            detail={**detail, "memory_changed": changed})
        self._memory_frozen[task_id] = snap
        return {"parent": parent["id"], "child": child,
                "memory_changed": changed, "memory": snap}

    def end_session(self, task_id: str, reason: str) -> None:
        """The task is finished with, so its session ends with it."""
        from .kernel import sessions

        row = sessions.current(self.conn, task_id)
        if row is not None:
            sessions.close(self.conn, self.log, row["id"], reason)
        self._memory_frozen.pop(task_id, None)

    def task_origin(self, task_id: str) -> str:
        """Where this task came from. From the ROW, not from memory.

        Cached because it is read on every tool call and can never change —
        there is no UPDATE path for the column, by design.
        """
        if task_id not in self._origin:
            row = self.conn.execute(
                "SELECT origin FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            self._origin[task_id] = (row[0] if row and row[0] else "local")
        return self._origin[task_id]

    def tool_policy(self, task_id: str):
        """What this agent may call: the ORIGIN FLOOR, plus anything narrower.

        The floor is applied on READ rather than written into `_tool_policy`
        at creation, and that is the whole A4 structural argument. A policy
        installed at creation lives in this process; the task row outlives it.
        Deriving here means a channel task parked overnight and resumed by a
        restarted process is still a channel task.

        It also makes the floor unbypassable rather than merely un-widened:
        `restrict_tools` refuses to widen, but a caller that never calls it at
        all would previously have got UNRESTRICTED. Now it gets the floor.
        """
        from .security.origin import policy_for

        floor = policy_for(self.task_origin(task_id))
        extra = self._tool_policy.get(task_id)
        if extra is None:
            return floor
        return extra.narrowed_by(floor.denied, floor.reason)

    def restrict_tools(self, task_id: str, policy) -> None:
        """Set a task's policy. Refuses to WIDEN an existing one.

        The refusal is the invariant, not a nicety: if a policy could be
        replaced rather than narrowed, an agent could delegate to itself and
        come back with more authority than it started with.
        """
        from .security.exposure import ToolNotExposed

        current = self._tool_policy.get(task_id)
        if current is not None and not set(current.denied) <= set(policy.denied):
            raise ToolNotExposed(
                f"refusing to widen the tool policy for {task_id}: "
                f"{sorted(set(current.denied) - set(policy.denied))} would "
                "become available again. Policies may only narrow."
            )
        self._tool_policy[task_id] = policy

    def taint_tracker(self, task_id: str) -> TaintTracker:
        """Per-task provenance, seeded from the task's ORIGIN.

        A4.2 — a message from a channel is UNTRUSTED, not USER. The goal of a
        channel task IS that message, so it is observed as untrusted content
        the first time anyone asks for the tracker. Doing it here rather than
        in the bot is the same argument as `tool_policy`: this is the choke
        point every execution path goes through, and the bot is one caller.

        The consequence is deliberate and worth stating plainly: any tool
        argument that quotes the message carries UNTRUSTED, so a channel task
        cannot write a file whose content is copied from the request. It can
        read, search, fetch, and answer. That is what "strictest surface in
        the project" costs, and softening it here would make the taint tier a
        label rather than a control.
        """
        if task_id not in self._taint:
            from .security.origin import is_channel

            tracker = TaintTracker()
            if is_channel(self.task_origin(task_id)):
                row = self.conn.execute(
                    "SELECT contract_json FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
                if row:
                    goal = json.loads(row[0]).get("goal", "")
                    tracker.observe(goal, Taint.UNTRUSTED)
            # DR1, LIFETIME AXIS. Everything this task has observed as
            # untrusted, REBUILT FROM THE RECORD rather than remembered.
            #
            # The tracker used to be a dict entry in one process. A resumed
            # task in a new process started with an empty one, and got away
            # with it only because a resumed task also forgets its own history
            # - two gaps cancelling. Phase 9 persists history across session
            # boundaries, which ends the cancellation, so this is fixed first.
            #
            # From `artifact.written` EVENTS, not artifact rows: rows are
            # content-addressed and keep the first writer only (D23), while the
            # event carries this write's own taint since 2026-09-13. An older
            # event has no taint field, and then the row's is the best there is.
            for content, ev_taint, row_taint in self.conn.execute(
                """SELECT a.content, json_extract(e.payload_json, '$.taint'),
                          a.taint
                   FROM events e
                   JOIN artifacts a ON a.id = json_extract(e.payload_json, '$.id')
                   WHERE e.task_id = ? AND e.type = 'artifact.written'
                   ORDER BY e.seq""",
                (task_id,),
            ):
                if (ev_taint or row_taint) == Taint.UNTRUSTED.value:
                    tracker.observe(content, Taint.UNTRUSTED)
            self._taint[task_id] = tracker
        return self._taint[task_id]

    # -- skills ------------------------------------------------------------

    def baseline_skills(self) -> list[dict]:
        return self.skills.promoted()

    def set_frozen_skills(self, task_id: str, skills: list[dict]) -> None:
        """An ASSIGNED skill set: one arm of a paired trial. Recorded on the
        chain the moment it is assigned - the empty set included, because
        "frozen to nothing" and "never frozen" must not look alike."""
        self._frozen_skills[task_id] = list(skills)
        self._record_frozen_skills(task_id, skills, source="assigned")

    def _record_frozen_skills(self, task_id: str, skills: list[dict],
                              source: str) -> None:
        self.log.append(task_id, "skills.frozen", {
            "source": source,
            "skills": [{"id": s.get("id", ""), "name": s["name"],
                        "version": s["version"]} for s in skills],
        })

    def _recorded_skills(self, task_id: str) -> list[dict] | None:
        """The set this task was frozen with, rebuilt byte-for-byte; None if
        it was never frozen.

        A skill id IS the hash of its body, and `load(verify=True)` checks it,
        so loading by id is already a byte-level commitment. Anything that no
        longer loads raises SkillSetChanged rather than being substituted.
        """
        from .skills.store import SkillIntegrityError, SkillSetChanged

        row = self.conn.execute(
            """SELECT payload_json FROM events
               WHERE task_id=? AND type='skills.frozen'
               ORDER BY seq DESC LIMIT 1""", (task_id,)).fetchone()
        if row is None:
            return None
        out = []
        for ref in json.loads(row[0])["skills"]:
            try:
                s = self.skills.load(ref["id"], verify=True)
            except (KeyError, SkillIntegrityError) as e:
                raise SkillSetChanged(
                    f"task {task_id} was frozen with {ref['name']} "
                    f"v{ref['version']} ({ref['id'][:12]}); {e}") from e
            out.append(s)
        return out

    def freeze_skills(self, task) -> list[dict]:
        """Frozen at task start — a promotion mid-task would void the cache.

        And DURABLE. This was a dict entry, which for a paired trial meant the
        arm assignment lived in one process: a control arm resumed elsewhere
        would have retrieved the promoted skills and become a skill arm, and a
        skill arm testing a CANDIDATE would have lost it, since retrieval only
        returns promoted skills. Both silently. See `skills.frozen`.
        """
        if task.id in self._frozen_skills:
            chosen = self._frozen_skills[task.id]
        else:
            recorded = self._recorded_skills(task.id)
            if recorded is not None:
                chosen = recorded
            else:
                from .skills.retrieve import retrieve

                chosen = retrieve(self, task.contract.goal)
                self._record_frozen_skills(task.id, chosen, source="retrieved")
            self._frozen_skills[task.id] = chosen
        for s in chosen:
            self.log.append(
                task.id,
                "skill.loaded",
                {"skill_id": s.get("id", ""), "name": s["name"], "version": s["version"]},
            )
        return chosen

    def record_skill_invocations(self, task_id: str, success: bool) -> None:
        """Record that each frozen skill was used, and whether the task worked.

        M9.5's decay pass demotes skills that go unused or regress. It reads
        `skill_invocations`, so a system that never writes there demotes every
        skill it has the first time decay runs.
        """
        for s in self._frozen_skills.get(task_id, []):
            sid = s.get("id")
            if sid:
                self.skills.record_invocation(sid, task_id, success)

    def gate_queue(self) -> list[str]:
        """Candidates waiting for the gate. DERIVED from the store.

        This was a list on the context that `generate_skill` appended to, so
        "handed to the gate" held for exactly as long as the generating process
        lived - and nothing in production ever read it. A candidate's status IS
        the queue: durable, the thing the gate changes, and unable to disagree
        with itself.
        """
        return [s["id"] for s in self.skills.by_status("candidate")]

    def record_promotion(self, skill_id: str, detail: dict) -> None:
        path = Path(self.config.root) / "runs" / "promotions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"skill_id": skill_id, "at": time.time(), **detail}) + "\n")

    # `research_retrieve`, M11's grounding step, was retired with the generator
    # it fed (2026-09-15, FINDINGS 1.27). It is at ce624c7 in git.

    # -- ledger seeding ----------------------------------------------------

    def seed_entries(self, task) -> list[LedgerEntry]:
        entries = [
            LedgerEntry.note(
                f"Objective: {task.contract.goal}\n"
                f"Success criterion: {task.contract.success_criterion}\n"
                f"Scope: {', '.join(task.contract.artifact_scope) or '(none declared)'}",
                pinned=True,
            )
        ]
        plan = self.approved_plan(task)
        if plan is not None:
            entries.append(
                LedgerEntry.note("Approved plan:\n" + plan.render(), pinned=True)
            )
        return entries

    def approved_plan(self, task):
        from .plan.planner import load_plan

        return load_plan(self, task.id, status="approved")

    # -- overflow ----------------------------------------------------------

    def handle_overflow_by_delegation(self, task, entries: list[LedgerEntry]) -> list[LedgerEntry]:
        """M3.4 says overflow is a delegation trigger, and M10 says most
        delegations should be refused. Both are right: try the contract, and
        when it fails — which is the common case — compact hard and say so.
        """
        from .delegate.contracts import SubtaskPlan, may_delegate
        from .kernel.types import Budget
        from .ledger.compact import compact

        bulky = [e for e in entries if e.kind == "observation" and not e.pinned]
        isolated = sum(e.n_tokens for e in bulky)
        sub = SubtaskPlan(
            goal=f"Extract only what bears on: {task.contract.goal}",
            estimated_irrelevant_tokens=isolated,
            output_schema={"type": "object", "properties": {"findings": {"type": "string"}}},
            success_criterion="json_schema",
            budget=Budget(usd=task.budget.remaining_usd() * 0.3,
                          seconds=int(task.budget.remaining_seconds() * 0.3)),
            is_tightly_coupled=len(bulky) <= 2,
            artifact_scope=task.contract.artifact_scope,
        )
        ok, why = may_delegate(self, task, sub)
        if not ok:
            # The SECOND route into compaction. Gated here as well as at the
            # loop's session boundary, or the compaction arm would report
            # "without compaction" while every refused delegation went on
            # tombstoning - DR3: guard the bytes, not one route to them.
            if not self.config.mechanisms.compaction:
                self.log.append(task.id, "compaction.skipped",
                                {"where": "delegation_refused", "why": why,
                                 "n_entries": len(entries),
                                 "reason": "compaction ablated (G1 arm)"})
                return entries
            target = int(self.window_for("cloud") * 0.18)
            out = compact(entries, target_tokens=target)
            out.append(
                LedgerEntry.note(
                    f"Context exceeded the fill ratio; delegation was refused "
                    f"({why}), so older entries were tombstoned. Pull any back "
                    f"with expand_artifact.",
                )
            )
            return out

        from .delegate.contracts import run_delegated

        result = run_delegated(self, task, sub)
        kept = [e for e in entries if e.pinned]
        kept.append(LedgerEntry.observation(result.render(), taint=Taint.USER, label="delegate"))
        return kept

    # -- review / approval -------------------------------------------------

    def compact_transcript(self, task_id: str, limit: int = 40) -> str:
        """Evidence for the reviewer: what was DONE. Never the agent's reasoning.

        M13.3 rule 2. Model text is excluded on purpose — a reviewer that reads
        the agent's own account of itself is reviewing a story.
        """
        rows = self.conn.execute(
            """SELECT type, payload_json FROM events
               WHERE task_id=? AND type IN
                 ('tool.intent','tool.receipt','verify.failed','verify.passed',
                  'human.denied','deviation.detected','artifact.written')
               ORDER BY seq DESC LIMIT ?""",
            (task_id, limit),
        ).fetchall()
        held_out = bool(getattr(self.config, "grader_held_out", False))
        lines = []
        for t, p in reversed(rows):
            payload = json.loads(p)
            if t.startswith("verify."):
                # A REVIEWER IS A MODEL, and its reason is handed back to the
                # executor as an observation - so anything rendered here can
                # reach the thing being graded one hop later. `detail` is the
                # verifier's full text, which is the answer key when the
                # verifier grades; it is the operator's, and never rendered.
                # Held out, the score goes too: pass/fail is the whole of it.
                payload.pop("detail", None)
                if held_out:
                    payload.pop("score", None)
            lines.append(f"{t}: {json.dumps(payload)[:300]}")
        return "\n".join(lines)

    def request_approval(self, task, tool, args: dict, reason: str, step: int) -> str:
        """Parking costs nothing: the task is persisted and no process spins."""
        from .security.capabilities import render_preview

        approval_id = "a_" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO approvals(id, task_id, step_id, tool, args_json, preview,
                                     reason, status, decided_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                approval_id, task.id, str(step), tool.name,
                json.dumps(args, default=str), render_preview(tool, args),
                reason, "pending", None, time.time(),
            ),
        )
        self.conn.commit()
        return approval_id

    def pending_approvals(self, task_id: str | None = None) -> list[dict]:
        q = """SELECT id, task_id, step_id, tool, preview, reason, created_at
               FROM approvals WHERE status='pending'"""
        args: tuple = ()
        if task_id:
            q += " AND task_id=?"
            args = (task_id,)
        return [
            {"id": r[0], "task_id": r[1], "step_id": r[2], "tool": r[3],
             "preview": r[4], "reason": r[5], "created_at": r[6]}
            for r in self.conn.execute(q + " ORDER BY created_at", args).fetchall()
        ]

    def resolve_approval(self, approval_id: str, approve: bool) -> dict:
        """A decision is FINAL. Deciding twice is refused, not applied.

        A4.3 found this. In the console an approval is a row in a live list
        that disappears once decided, so nobody could press it twice. An
        inline button in a chat stays on screen forever — scroll back a week
        and the Approve is still there, still tappable, and the second press
        was landing: the UPDATE had no guard on the current status, so a
        `deny` after an `approve` silently flipped the row and appended a
        second, contradictory human decision to the chain.

        The chain would not have caught it either. Both events are validly
        appended and hash correctly; the log would simply contain a human
        approving and then denying the same action, with nothing to say which
        one the executor acted on.
        """
        row = self.conn.execute(
            "SELECT task_id, tool, status FROM approvals WHERE id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise KeyError(approval_id)
        task_id, tool, status = row
        if status != "pending":
            return {"approval_id": approval_id, "task_id": task_id,
                    "approved": status == "approved", "already_decided": True,
                    "decision": status}
        self.conn.execute(
            "UPDATE approvals SET status=?, decided_at=? WHERE id=?",
            ("approved" if approve else "denied", time.time(), approval_id),
        )
        self.conn.commit()
        self.log.append(
            task_id,
            "human.approved" if approve else "human.denied",
            {"approval_id": approval_id, "tool": tool},
        )
        # A human decision is anchored: it is a point we must not be able to
        # rewrite later.
        from .kernel.integrity import checkpoint

        checkpoint(self.log, self.anchor, task_id)
        return {"approval_id": approval_id, "task_id": task_id,
                "approved": approve, "already_decided": False}
