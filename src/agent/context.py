from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .assure.mesh import VerificationMesh
from .config import Config, load_config, load_dotenv
from .execute.tools import ToolRegistry, apply_profile, default_registry
from .instrument.telemetry import Telemetry, compute_cost, record_call
from .kernel.db import open_db
from .kernel.events import EventLog, sha256_hex
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
    out = ToolRegistry()
    for name in registry.names():
        if name not in names:
            out.register(registry.get(name))
    return out


MAX_PROJECT_NOTES = 8000


class AgentContext:
    def __init__(
        self,
        config: Config,
        conn: sqlite3.Connection | None = None,
        backends: dict | None = None,
        tools: ToolRegistry | None = None,
    ):
        self.config = config
        self.conn = conn or open_db(config.resolve_state(config.db_path))
        self.log = EventLog(self.conn)
        self.anchor = Anchor(config.resolve_state(config.anchor_path))
        self.backends = backends if backends is not None else build_backends(config)
        self.tools = tools or default_registry()
        executor = self.backends.get("cloud")
        self.tool_profile = executor.cfg.tool_profile if executor else "full"
        self.tools = apply_profile(self.tools, self.tool_profile)
        if not config.mechanisms.code_as_action:
            self.tools = _without(self.tools, "run_code")
        self.skills = SkillStore(self.conn, config.resolve(config.skills_path))
        self.userdata = UserStore(self.conn)
        from .memory.store import MemoryStore
        from .paths import user_data_dir

        self.memory = MemoryStore(user_data_dir() / "memory")
        self._memory_frozen: dict[str, dict] = {}
        self._notes_frozen: dict[str, str] = {}
        self.mcp_servers: list = []
        self.telemetry = Telemetry(self.conn, cloud_backend_names(config),
                                   cache_reporting_backend_names(config))
        self.mesh = VerificationMesh(self)
        self.router = Router(self)

        self.phase: str = "execute"
        self.executing_backend: str | None = None
        self.web_fetcher = None

        self._tool_policy: dict[str, object] = {}
        self._origin: dict[str, str] = {}
        self._taint: dict[str, TaintTracker] = {}
        self._frozen_skills: dict[str, list[dict]] = {}

    @classmethod
    def open(cls, config_path: str | Path = "config.yaml",
             profile: str | None = None) -> "AgentContext":
        load_dotenv(Path(config_path).parent / ".env")
        return cls(load_config(config_path, profile=profile))

    def close(self) -> None:
        from .execute.mcp import close_all

        close_all(self)
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

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
        spend(self.conn, self.log, task, usd=cost)
        return cost

    def call_model(self, task, backend, purpose: str, escalated: bool = False,
                   **kwargs) -> ModelResponse:
        from .instrument.telemetry import abort_call, begin_call, complete_call
        from .models.stall import ModelStall

        b = self.backends[backend] if isinstance(backend, str) else backend
        task_id = task if isinstance(task, str) else task.id
        from .tokens import count_tokens

        est = count_tokens(kwargs.get("prefix") or "") + count_tokens(kwargs.get("tail") or "")
        cid = begin_call(self.conn, task_id, b.name, purpose, escalated=escalated,
                         thinking=b.cfg.thinking, model=b.cfg.model, est_input_tokens=est)
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

    def freeze_project_notes(self, task) -> str:
        if task.id not in self._notes_frozen:
            self._notes_frozen[task.id] = self._load_project_notes(task)
        return self._notes_frozen[task.id]

    def _load_project_notes(self, task) -> str:
        from .security.capabilities import within_scope

        path = Path(self.config.root) / "AGENTS.md"
        if not self.config.mechanisms.agents_md or not path.is_file():
            return ""
        why = ""
        if path.is_symlink() or path.stat().st_nlink > 1:
            why = "a symlink or hard link: it may be a file the task can write"
        elif within_scope({"path": "AGENTS.md"}, task.contract.artifact_scope, self.config.root)[0]:
            why = "inside artifact_scope: the task can write it, so it is not an instruction"
        if why:
            self.log.append(task.id, "agents_md.skipped", {"why": why})
            return ""
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        cut = len(text) > MAX_PROJECT_NOTES
        if cut:
            text = text[:MAX_PROJECT_NOTES] + chr(10) + "[truncated]"
        self.log.append(task.id, "agents_md.loaded",
                        {"bytes": len(raw), "sha256": sha256_hex(raw.decode("utf-8", errors="replace")), "truncated": cut})
        return text

    def freeze_memory(self, task_id: str) -> dict:
        if task_id not in self._memory_frozen:
            from .kernel import sessions

            open_row = sessions.current(self.conn, task_id)
            if open_row is not None:
                self._memory_frozen[task_id] = json.loads(open_row["memory_json"])
            else:
                snap = self._snapshot_memory(task_id)
                sessions.open_session(self.conn, self.log, task_id, snap,
                                      detail={"long_files": self.config.long_files})
                self._memory_frozen[task_id] = snap
        return self._memory_frozen[task_id]

    def _snapshot_memory(self, task_id: str) -> dict:
        from .memory.store import INTACT, check_fingerprint, tier1_snapshot

        isolated = bool(getattr(self.config, "isolate_retrieval", False))
        state = check_fingerprint(self.memory)
        if state != INTACT:
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
        from .kernel import sessions

        self.freeze_memory(task_id)
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
        from .kernel import sessions

        row = sessions.current(self.conn, task_id)
        if row is not None:
            sessions.close(self.conn, self.log, row["id"], reason)
        self._memory_frozen.pop(task_id, None)

    def task_origin(self, task_id: str) -> str:
        if task_id not in self._origin:
            row = self.conn.execute(
                "SELECT origin FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            self._origin[task_id] = (row[0] if row and row[0] else "local")
        return self._origin[task_id]

    @property
    def code_as_action(self) -> bool:
        return self.config.mechanisms.code_as_action and self.tool_profile != "small"

    def tool_policy(self, task_id: str):
        from .security.origin import policy_for

        floor = policy_for(self.task_origin(task_id))
        extra = self._tool_policy.get(task_id)
        if extra is None:
            return floor
        return extra.narrowed_by(floor.denied, floor.reason)

    def restrict_tools(self, task_id: str, policy) -> None:
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

    def baseline_skills(self) -> list[dict]:
        return self.skills.promoted()

    def set_frozen_skills(self, task_id: str, skills: list[dict]) -> None:
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
        for s in self._frozen_skills.get(task_id, []):
            sid = s.get("id")
            if sid:
                self.skills.record_invocation(sid, task_id, success)

    def record_promotion(self, skill_id: str, detail: dict) -> None:
        path = self.config.resolve_state("runs") / "promotions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"skill_id": skill_id, "at": time.time(), **detail}) + "\n")

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
        past = self._conversation_history(task.id)
        if past:
            aid, text = past
            entries.append(LedgerEntry.observation(text, artifact_id=aid, taint=Taint.UNTRUSTED,
                                                   label="conversation"))
        return entries

    def _conversation_history(self, task_id: str) -> tuple[str, str] | None:
        from .control.conversations import history
        from .ledger.artifacts import write_artifact

        row = self.conn.execute(
            """SELECT a.id, a.content FROM events e JOIN artifacts a
                 ON a.id = json_extract(e.payload_json, '$.artifact')
                WHERE e.task_id=? AND e.type='conversation.history'""", (task_id,)).fetchone()
        if row:
            return row[0], row[1]
        text, n = history(self.conn, task_id)
        if not n:
            return None
        aid = write_artifact(self.conn, task_id, "conversation", text, Taint.UNTRUSTED,
                             {"source": "conversation"})
        self.log.append(task_id, "artifact.written",
                        {"id": aid, "kind": "conversation", "taint": Taint.UNTRUSTED.value})
        self.log.append(task_id, "conversation.history", {"artifact": aid, "exchanges": n})
        self.taint_tracker(task_id).observe(text, Taint.UNTRUSTED)
        return aid, text

    def approved_plan(self, task):
        from .plan.planner import load_plan

        return load_plan(self, task.id, status="approved")

    def handle_overflow_by_delegation(self, task, entries: list[LedgerEntry]) -> list[LedgerEntry]:
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

    def compact_transcript(self, task_id: str, limit: int = 40) -> str:
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
                payload.pop("detail", None)
                if held_out:
                    payload.pop("score", None)
            lines.append(f"{t}: {json.dumps(payload)[:300]}")
        return "\n".join(lines)

    def request_approval(self, task, tool, args: dict, reason: str, step: int) -> str:
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
            for r in self.conn.execute(q + " ORDER BY created_at, rowid", args).fetchall()
        ]

    def resolve_approval(self, approval_id: str, approve: bool) -> dict:
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
        from .kernel.integrity import checkpoint

        checkpoint(self.log, self.anchor, task_id)
        return {"approval_id": approval_id, "task_id": task_id,
                "approved": approve, "already_decided": False}
