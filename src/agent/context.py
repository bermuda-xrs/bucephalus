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
        # Our own fetcher, on purpose: content must enter through the tool path
        # so it lands in the ledger as UNTRUSTED and the gate can see where it
        # came from. See net/fetch.py for why a server-side fetch tool is not
        # a drop-in replacement.
        from .net.fetch import default_fetcher

        self.web_fetcher = default_fetcher

        self._taint: dict[str, TaintTracker] = {}
        self._frozen_skills: dict[str, list[dict]] = {}
        self._gate_queue: list[str] = []

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

    # -- taint -------------------------------------------------------------

    def freeze_memory(self, task_id: str) -> dict:
        """Tier 1 as of task start, held for the life of the task.

        The same discipline as `freeze_skills`, for the same reason: anything
        the prefix is built from must not change while the task runs. Frozen
        per task rather than per context because a delegated child is its own
        task with its own start.
        """
        if task_id not in self._memory_frozen:
            from .memory.store import INTACT, check_fingerprint, tier1_snapshot

            isolated = bool(getattr(self.config, "isolate_retrieval", False))
            state = check_fingerprint(self.memory)
            if state != INTACT:
                # REFUSE TO LOAD, CONTINUE WITHOUT IT.
                #
                # Not a halt, and the distinction is the point. A chain break
                # destroys the ability to VERIFY, so anything concluded from
                # the run is unfounded and stopping is the only honest move.
                # Memory tampering destroys the trustworthiness of ONE INPUT,
                # and the run stays fully auditable without it. Dropping a
                # corrupted input and carrying on is what this system already
                # does with a corrupted observation.
                #
                # Continuing WITH the content is the attack succeeding, and
                # halting makes a text file anyone can edit into a denial of
                # service. Degraded-but-safe is the third option and it is the
                # right one.
                self.log.append(task_id, "memory.tampered",
                                {"state": state,
                                 "recorded_present": (self.memory.root / ".fingerprint").exists(),
                                 "current": self.memory.fingerprint(),
                                 "action": "tier 1 refused; task continues "
                                           "without persistent memory"})
                isolated = True
            self._memory_frozen[task_id] = tier1_snapshot(
                self.memory, isolated=isolated,
            )
        return self._memory_frozen[task_id]

    def taint_tracker(self, task_id: str) -> TaintTracker:
        if task_id not in self._taint:
            self._taint[task_id] = TaintTracker()
        return self._taint[task_id]

    # -- skills ------------------------------------------------------------

    def baseline_skills(self) -> list[dict]:
        return self.skills.promoted()

    def set_frozen_skills(self, task_id: str, skills: list[dict]) -> None:
        self._frozen_skills[task_id] = list(skills)

    def freeze_skills(self, task) -> list[dict]:
        """Frozen at task start — a promotion mid-task would void the cache."""
        if task.id in self._frozen_skills:
            chosen = self._frozen_skills[task.id]
        else:
            from .skills.retrieve import retrieve

            chosen = retrieve(self, task.contract.goal)
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

    def enqueue_for_gate(self, skill_id: str) -> None:
        self._gate_queue.append(skill_id)

    def gate_queue(self) -> list[str]:
        return list(self._gate_queue)

    def record_promotion(self, skill_id: str, detail: dict) -> None:
        path = Path(self.config.root) / "runs" / "promotions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"skill_id": skill_id, "at": time.time(), **detail}) + "\n")

    def research_retrieve(self, query: str, k: int = 5) -> list:
        """Grounding sources for M11.

        Two places to ground from, both local and both auditable:

        1. `grounding/` in the project — notes, docs, transcripts you put there
           deliberately. Matched on lexical overlap with the query.
        2. Any http(s) URL appearing in the query itself, fetched through our
           own fetcher.

        There is deliberately no search engine here. The spec's finding is that
        self-generated skills drift negative when drafted from model priors;
        substituting "whatever a search API returned" for priors swaps one
        ungrounded source for another. Sources you chose beat sources you
        happened to get back.
        """
        from .skills.generate import Source

        sources: list[Source] = []
        today = time.strftime("%Y-%m-%d")

        # Grounding notes authored in the UI come first — they are the ones
        # someone deliberately chose for this.
        for name, body in self.userdata.grounding_sources(query, k=k):
            sources.append(Source(url=f"grounding://{name}", content=body,
                                  retrieved=today))
        if len(sources) >= k:
            return sources[:k]

        for url in re.findall(r"https?://[^\s)>\]]+", query):
            body = self.web_fetcher(url) if self.web_fetcher else ""
            if body and not body.startswith("[fetch failed]"):
                sources.append(Source(url=url, content=body, retrieved=today))
            if len(sources) >= k:
                return sources

        gdir = Path(self.config.root) / "grounding"
        if gdir.is_dir():
            terms = {w.lower() for w in re.findall(r"\w{4,}", query)}
            scored = []
            for p in sorted(gdir.rglob("*")):
                if not p.is_file() or p.suffix.lower() not in (".md", ".txt", ".rst"):
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
                hay = {w.lower() for w in re.findall(r"\w{4,}", text)}
                overlap = len(terms & hay) / max(1, len(terms))
                if overlap > 0:
                    scored.append((overlap, p, text))
            for _, p, text in sorted(scored, key=lambda t: -t[0])[: k - len(sources)]:
                sources.append(
                    Source(url=f"file://{p.as_posix()}", content=text, retrieved=today)
                )

        if not sources:
            raise RuntimeError(
                f"no grounding sources for {query!r}. Add grounding notes in the "
                f"UI (Grounding tab), put files in {gdir}, include a URL in the "
                "pattern description, or pass sources= explicitly. Drafting from "
                "model priors is exactly the failure mode M9 exists to catch."
            )
        return sources

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
        lines = [f"{t}: {json.dumps(json.loads(p))[:300]}" for t, p in reversed(rows)]
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
        row = self.conn.execute(
            "SELECT task_id, tool FROM approvals WHERE id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise KeyError(approval_id)
        task_id, tool = row
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
        return {"approval_id": approval_id, "task_id": task_id, "approved": approve}
