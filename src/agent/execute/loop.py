from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from ..kernel.events import canonical, sha256_hex
from ..kernel.integrity import IntegrityError, checkpoint, resume
from ..kernel.receipts import execute_once, idempotency_key, rewind_epoch
from ..models.ollama import ModelStall
from ..kernel.tasks import load_task, set_status, spend
from ..kernel.types import GateDecision, Taint, TaskStatus, join_taint
from ..ledger.artifacts import project, read_artifact, write_artifact
from ..ledger.compact import compact, enforce_ceiling
from ..ledger.entries import LedgerEntry, render_tail, sum_tokens
from ..ledger.prefix import PrefixBuilder, PrefixGuard, system_contract
from ..ledger.semantic_cache import cached_op
from ..security.capabilities import gate_tool_call, render_preview
from ..security.origin import is_unattended
from ..skills.store import SkillSetChanged
from ..security.exposure import ToolNotExposed
from ..security.exposure import check as exposure_check
from ..security.taint import QUARANTINE_PREFIX
from ..assure.verifiers import inspects
from ..tokens import Calibration, calibration, count_tokens
from .say import take as take_messages
from .schema import build_action_schema
from .. import stream

COMPRESS_PREFIX = """\
You compress tool output. You have no tools and you cannot act.

Keep, character for character: file paths, error messages, identifiers,
version numbers, line numbers, and hashes. Drop everything that does not bear
on the question. Do not paraphrase an error. Do not invent anything.

Output the kept material only. No preamble."""


class TaskPaused(Exception):
    def __init__(self, approval_id: str, reason: str):
        super().__init__(reason)
        self.approval_id = approval_id
        self.reason = reason


@dataclass
class ParsedAction:
    kind: str
    tool: str = ""
    args: dict = field(default_factory=dict)
    payload: Any = None
    raw: str = ""
    error: str = ""


@dataclass
class TaskResult:
    task_id: str
    status: str
    answer: Any = None
    steps: int = 0
    reason: str = ""
    approval_id: str | None = None


_ACTION_RX = re.compile(r"<action\s+tool=\"([\w.]+)\"\s*>(.*?)</action>", re.S)
_CODE_RX = re.compile(r"<code>(.*?)</code>", re.S)
_FINAL_RX = re.compile(r"<final>(.*?)</final>", re.S)

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["action", "code", "final"]},
        "tool": {"type": "string"},
        "args": {"type": "object"},
        "code": {"type": "string"},
        "answer": {},
    },
    "required": ["kind"],
}

ACTION_TAG_HINT = """\

Reply with exactly ONE block, nothing else:

  <action tool="read_file">{"path": "work/x.py"}</action>
  <code>
  # plain Python. No JSON, no quotes around it, no closing brace.
  import json
  </code>
  <final>{"answer": "..."}</final>

Inside <code> write ordinary Python exactly as you would in a file."""


ACTION_SCHEMA_HINT = """\

Answer with a single JSON object, not the tag syntax:
  {"kind": "action", "tool": "read_file", "args": {"path": "work/x.py"}}
  {"kind": "code",   "code": "..."}
  {"kind": "final",  "answer": {...}}"""

ACTION_SCHEMA_HINT_NO_CODE = """\

Answer with a single JSON object, not the tag syntax:
  {"kind": "action", "tool": "read_file", "args": {"path": "work/x.py"}}
  {"kind": "final",  "answer": {...}}"""


def _parse_json_action(text: str) -> ParsedAction | None:
    s = text.strip()
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "kind" not in obj:
        return None

    kind = obj.get("kind")
    if kind == "final":
        return ParsedAction("final", payload=obj.get("answer", obj), raw=s)
    if kind == "code":
        return ParsedAction("code", args={"code": obj.get("code", "")}, raw=s)
    if kind == "action":
        tool = obj.get("tool", "")
        args = obj.get("args") or {}
        if not tool:
            return ParsedAction("none", raw=s, error="action object has no 'tool'")
        if not isinstance(args, dict):
            return ParsedAction("none", raw=s, error="'args' must be an object")
        return ParsedAction("action", tool=tool, args=args, raw=s)
    return None


def parse_action(text: str) -> ParsedAction:
    parsed = _parse_json_action(text)
    if parsed is not None:
        return parsed

    m = _FINAL_RX.search(text)
    if m:
        body = m.group(1).strip()
        try:
            return ParsedAction("final", payload=json.loads(body), raw=body)
        except json.JSONDecodeError:
            return ParsedAction("final", payload={"answer": body}, raw=body)

    m = _ACTION_RX.search(text)
    if m:
        tool, body = m.group(1), m.group(2).strip()
        try:
            args = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            return ParsedAction(
                "none", raw=body, error=f"action body was not valid JSON: {e}"
            )
        if not isinstance(args, dict):
            return ParsedAction("none", raw=body, error="action body must be a JSON object")
        return ParsedAction("action", tool=tool, args=args, raw=body)

    m = _CODE_RX.search(text)
    if m:
        return ParsedAction("code", args={"code": m.group(1)}, raw=m.group(1))

    return ParsedAction(
        "none",
        raw=text,
        error=(
            "no <action>, <code>, or <final> block found, and the reply was not "
            "a JSON action object. Emit exactly ONE of:\n"
            '  <action tool="read_file">{"path": "work/x.py"}</action>\n'
            "  <code>...</code>\n"
            '  <final>{"answer": "..."}</final>\n'
            'or a single JSON object such as {"kind": "action", "tool": '
            '"read_file", "args": {"path": "work/x.py"}}.\n'
            "The tool name goes in the tag attribute, and the body is JSON."
        ),
    )


def _verified_state(ctx, task_id: str) -> dict:
    head = ctx.log.head(task_id)
    anchored = ctx.anchor.latest_chain(task_id)
    state = "none" if not anchored else ("matched" if anchored == head else "behind")
    return {"head": head[:16] if head else None, "anchor": state}


def run_task(ctx, task_id: str) -> TaskResult:
    task = load_task(ctx.conn, task_id)

    try:
        resume(ctx.conn, task.id, ctx.log, ctx.anchor)
    except IntegrityError:
        set_status(ctx.conn, ctx.log, task, TaskStatus.FAILED, "integrity_halt")
        raise
    ctx.log.append(task.id, "integrity.verified", _verified_state(ctx, task.id))

    set_status(ctx.conn, ctx.log, task, TaskStatus.RUNNING)
    checkpoint(ctx.log, ctx.anchor, task.id)

    stable = ctx.config.mechanisms.prefix_stability
    guard = PrefixGuard() if stable else None
    try:
        skills = ctx.freeze_skills(task)
    except SkillSetChanged:
        set_status(ctx.conn, ctx.log, task, TaskStatus.FAILED, "skill_set_changed")
        raise
    from .mcp import attach

    attach(ctx, task)
    memory = ctx.freeze_memory(task.id)
    notes = ctx.freeze_project_notes(task)
    exe = ctx.backends.get("cloud")
    cal = calibration(ctx.conn, "cloud", exe.cfg.model) if exe else Calibration()
    ctx.log.append(task.id, "tokens.calibration",
                   {"backend": "cloud", "model": exe.cfg.model if exe else None,
                    "n": cal.n, "slope": round(cal.slope, 4),
                    "intercept": round(cal.intercept, 1), "fitted": cal.fitted})
    entries: list[LedgerEntry] = list(ctx.seed_entries(task))
    plan = ctx.approved_plan(task)
    started = time.time()

    best_answer: Any = None
    best_score: float = -1.0
    score_history: list[float] = []
    charged_s = 0.0
    seen: dict[str, int] = {}

    try:
        if plan is not None:
            from ..plan.planner import verify_plan_pin

            plan = verify_plan_pin(ctx, task)
        entries.extend(_restore_park_seed(ctx, task, entries))
        entries.extend(_release_decided(ctx, task))

        for step in range(ctx.config.limits.max_steps):
            entries.extend(take_messages(ctx, task, step))
            elapsed = time.time() - started
            if elapsed > charged_s:
                spend(ctx.conn, ctx.log, task, seconds=elapsed - charged_s)
                charged_s = elapsed
            if task.budget.exhausted():
                return _final_sweep(ctx, task, "budget_exhausted",
                                    best_answer, step)
            if load_task(ctx.conn, task.id).status == TaskStatus.PAUSED:
                raise TaskPaused(None, "human requested")

            if plan is not None:
                from ..plan.planner import verify_plan_pin

                plan = verify_plan_pin(ctx, task)

            if not stable:
                skills = ctx.freeze_skills(task)
                memory = ctx._snapshot_memory(task.id)  # noqa: SLF001
            builder = PrefixBuilder(
                system_contract(ctx.code_as_action),
                skills,
                json.loads(task.contract.model_dump_json()),
                tool_manual=ctx.tools.manual(),
                memory=memory,
                project_notes=notes,
            )
            if guard is not None:
                guard.check(builder, task.id)
            prefix = builder.build()

            window = ctx.window_for("cloud")
            action = enforce_ceiling(
                *cal.apply(count_tokens(prefix), sum_tokens(entries)),
                window,
                ctx.config.limits.context_fill_ratio,
            )
            if ctx.config.compaction_probe_step == step and action != "compact":
                ctx.log.append(task.id, "compaction.skipped",
                               {"where": "probe", "step": step,
                                "real_action": action,
                                "prefix_tokens": count_tokens(prefix),
                                "tail_tokens": sum_tokens(entries),
                                "reason": "synthetic boundary forced here; the "
                                          "real ceiling was not crossed"})
                action = "compact"
            if action == "compact" and not ctx.config.mechanisms.compaction:
                ctx.log.append(task.id, "compaction.skipped",
                               {"where": "session_boundary", "step": step,
                                "prefix_tokens": count_tokens(prefix),
                                "tail_tokens": sum_tokens(entries),
                                "window": window,
                                "fill_ratio": ctx.config.limits.context_fill_ratio,
                                "reason": "compaction ablated (G1 arm)"})
            elif action == "compact":
                entries, memory, guard, builder, prefix = _session_boundary(
                    ctx, task, entries, window, step, plan, skills, builder,
                    prefix)
            elif action == "delegate":
                entries = ctx.handle_overflow_by_delegation(task, entries)

            tail = render_tail(entries)
            backend = ctx.router.choose("step", task, step)
            ctx.executing_backend = backend.name

            tail, step_schema = _envelope(
                ctx, backend, tail, not backend.cfg.requires_observation or _observed(ctx, task))
            ctx.log.append(
                task.id,
                "model.called",
                {
                    "backend": backend.name,
                    "purpose": "step",
                    "step": step,
                    "prefix_fp": builder.fingerprint(),
                    "est_input_tokens": count_tokens(prefix) + count_tokens(tail),
                },
            )

            stream.publish(task.id, "waiting", {"step": step,
                                                "backend": backend.name})
            resp = ctx.call_model(
                task, backend, "step",
                prefix=prefix,
                tail=tail,
                tools=ctx.tools.schemas(),
                max_tokens=backend.default_max_tokens(),
                temperature=0.0,
                schema=step_schema,
                on_chunk=stream.token_sink(task.id),
            )
            stream.publish(task.id, "step", {
                "step": step, "backend": backend.name,
                "output_tokens": resp.usage.output_tokens,
                "spent_usd": round(task.budget.spent_usd, 6),
                "spent_seconds": round(task.budget.spent_seconds, 1),
                "budget_usd": task.budget.usd,
                "budget_seconds": task.budget.seconds,
            })
            ctx.log.append(
                task.id,
                "model.returned",
                {"backend": backend.name, "output_tokens": resp.usage.output_tokens},
            )

            parsed = parse_action(resp.text)

            repeats = 0
            if ctx.config.mechanisms.stuck_detection and parsed.kind != "final":
                key = sha256_hex(canonical({"k": parsed.kind, "t": parsed.tool, "a": parsed.args,
                                            "r": parsed.raw if parsed.kind == "none" else ""}))
                seen[key] = repeats = seen.get(key, 0) + 1
                if repeats >= STUCK_HALT:
                    ctx.log.append(task.id, "stuck.detected",
                                   {"step": step, "kind": parsed.kind, "tool": parsed.tool, "repeats": repeats})
                    return _final_sweep(ctx, task, "stuck", best_answer, step + 1)

            if parsed.kind == "final" and backend.cfg.requires_observation and not _observed(ctx, task):
                ctx.log.append(task.id, "read_first.held", {"step": step})
                entries.append(LedgerEntry.observation(READ_FIRST_NOTE, taint=Taint.TRUSTED, label="read-first"))
                continue

            if parsed.kind == "final" and _must_verify_first(ctx, task):
                n = _verify_continuations(ctx, task) + 1
                ctx.log.append(task.id, "verify.continued", {"step": step, "n": n})
                entries.append(LedgerEntry.observation(
                    "You changed files but have not run a test, lint or the program since. Run one now "
                    "with run_command, or answer again if none applies (" + str(MAX_VERIFY_CONTINUATIONS - n)
                    + " reminder(s) left).", taint=Taint.TRUSTED, label="verify-before-stop"))
                continue

            if parsed.kind == "final" and ctx.config.hooks.stop and _stop_blocks(ctx, task) < MAX_STOP_BLOCKS:
                from .hooks import run_hooks

                answer = parsed.payload if isinstance(parsed.payload, str) else json.dumps(parsed.payload)
                held, why = run_hooks(ctx, task, "stop", answer=answer)
                if not held:
                    ctx.log.append(task.id, "hook.stop_blocked", {"step": step, "why": why[:300]})
                    entries.append(LedgerEntry.observation(
                        "A stop hook configured by the user held your answer back:" + chr(10) + why
                        + chr(10) + "Deal with that, then answer again.", taint=Taint.UNTRUSTED, label="hook"))
                    continue

            if parsed.kind == "final":
                result = parsed.payload
                artifact_text = (
                    result if isinstance(result, str) else json.dumps(result, indent=2)
                )
                ok, report = _verify(ctx, task, artifact_text)
                if ok:
                    return finish(ctx, task, "done", answer=result, steps=step + 1)

                if report.score > best_score:
                    best_score, best_answer = report.score, result
                score_history.append(best_score)

                if len(score_history) >= 3 and (
                    max(score_history[-3:]) - min(score_history[-3:]) < 1e-9
                ):
                    return finish(ctx, task, "plateau", answer=best_answer,
                                  steps=step + 1)

                if plan is not None:
                    from ..plan.deviation import on_deviation

                    step_id = plan.steps[0].id if plan.steps else "1"
                    dev = on_deviation(ctx, task, plan, step_id, report.observation)
                    if dev.action == "park_for_replan":
                        return finish(ctx, task, "needs_replan", answer=best_answer,
                                      steps=step + 1)

                entries.append(
                    LedgerEntry.observation(
                        ctx.mesh.feedback_for_model(report),
                        taint=Taint.TRUSTED,
                        label="verifier",
                    )
                )
                continue

            if parsed.kind == "none":
                entries.append(
                    LedgerEntry.observation(
                        parsed.error, taint=Taint.TRUSTED, label="parser"
                    )
                )
                _warn_if_stuck(ctx, task, entries, step, repeats, parsed)
                continue

            if _changes_something(ctx, parsed):
                _checkpoint(ctx, task, step, entries)
            seq0 = _last_receipt_seq(ctx, task)
            obs = execute_action(ctx, task, parsed, step, plan)
            entries.append(obs)
            if _changed_since(ctx, task, seq0):
                seen.clear()
            else:
                _warn_if_stuck(ctx, task, entries, step, repeats, parsed)
            if parsed.tool == "update_plan" and obs.label == "update_plan":
                entries = _with_checklist(ctx, task, entries, parsed.args)

        return _final_sweep(ctx, task, "max_steps", best_answer,
                            ctx.config.limits.max_steps)

    except IntegrityError:
        set_status(ctx.conn, ctx.log, task, TaskStatus.FAILED, "integrity_halt")
        raise

    except TaskPaused as p:
        spend(ctx.conn, ctx.log, task, seconds=time.time() - started)
        _write_park_seed(ctx, task, entries)
        set_status(ctx.conn, ctx.log, task, TaskStatus.PAUSED, p.reason)
        checkpoint(ctx.log, ctx.anchor, task.id)
        return TaskResult(
            task.id, "paused", reason=p.reason, approval_id=p.approval_id
        )

    except ModelStall as e:
        ctx.log.append(task.id, "task.model_stalled",
                       {"error": f"{type(e).__name__}: {e}",
                        "observed_tok_s": getattr(e, "observed_rate", None),
                        "median_tok_s": getattr(e, "median_rate", None)})
        spend(ctx.conn, ctx.log, task, seconds=time.time() - started)

        swept = _final_sweep(ctx, task, "model_stalled", best_answer, 0)
        if swept.status in ("done", "done_unannounced"):
            return swept

        raise

    except Exception as e:  # noqa: BLE001
        ctx.log.append(task.id, "task.crashed",
                       {"error": f"{type(e).__name__}: {e}"})
        spend(ctx.conn, ctx.log, task, seconds=time.time() - started)
        return _final_sweep(ctx, task, "crashed", best_answer, 0)

    finally:
        ctx.executing_backend = None


MAX_VERIFY_CONTINUATIONS = 3
MAX_STOP_BLOCKS = 3
READ_FIRST_NOTE = ("No answer is accepted yet: look at something first with a tool (read a file, search, "
                   "run a command), then answer from what you saw.")


def _observed(ctx, task) -> bool:
    return ctx.conn.execute("SELECT 1 FROM events WHERE task_id=? AND type='tool.receipt' LIMIT 1",
                            (task.id,)).fetchone() is not None


def _stop_blocks(ctx, task) -> int:
    return ctx.conn.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND type='hook.stop_blocked'",
                            (task.id,)).fetchone()[0]
STUCK_WARN = 3
STUCK_HALT = 6


def _last_receipt_seq(ctx, task) -> int:
    return ctx.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events WHERE task_id=? AND type='tool.receipt'",
                            (task.id,)).fetchone()[0]


def _changed_since(ctx, task, seq: int) -> bool:
    row = ctx.conn.execute("SELECT payload_json FROM events WHERE task_id=? AND type='tool.receipt' AND seq>? "
                           "ORDER BY seq DESC LIMIT 1", (task.id, seq)).fetchone()
    return bool(row and json.loads(row[0]).get("changed"))


def _warn_if_stuck(ctx, task, entries, step, repeats, parsed) -> None:
    if repeats != STUCK_WARN:
        return
    ctx.log.append(task.id, "stuck.warned", {"step": step, "kind": parsed.kind, "tool": parsed.tool,
                                             "repeats": repeats})
    entries.append(LedgerEntry.observation(
        f"You have now made this exact same call {repeats} times and nothing has changed. Change approach: "
        f"try something different, or answer with what you have. At {STUCK_HALT} the task is stopped.",
        taint=Taint.TRUSTED, label="stuck"))


def _verify_continuations(ctx, task) -> int:
    return ctx.conn.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND type='verify.continued'",
                            (task.id,)).fetchone()[0]


def _must_verify_first(ctx, task) -> bool:
    if not ctx.config.mechanisms.verify_before_stop or "run_command" not in ctx.tools:
        return False
    if _verify_continuations(ctx, task) >= MAX_VERIFY_CONTINUATIONS:
        return False
    dirty = False
    for (payload,) in ctx.conn.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND type='tool.receipt' ORDER BY seq", (task.id,)):
        d = json.loads(payload)
        if d.get("changed"):
            dirty = True
        if d.get("verify"):
            dirty = False
    return dirty


def _with_checklist(ctx, task, entries, args) -> list[LedgerEntry]:
    from .tools import render_checklist

    note = LedgerEntry.note("Your working checklist (the approved plan is unchanged):" + chr(10)
                            + render_checklist(args["items"][:30]), pinned=True)
    note.label = "checklist"
    note.taint = ctx.taint_tracker(task.id).args_taint(args)
    return [e for e in entries if e.label != "checklist"] + [note]


def _final_sweep(ctx, task, status: str, best_answer, steps: int) -> TaskResult:
    kind = inspects(ctx.mesh.registry.get(task.contract.success_criterion))
    if kind == "nothing" or (not best_answer and kind != "workspace"):
        return finish(ctx, task, status, answer=best_answer, steps=steps)

    ok, report = _verify(ctx, task, best_answer if isinstance(best_answer, str)
                         else json.dumps(best_answer, indent=2) if best_answer
                         else "")
    if ok:
        ctx.log.append(task.id, "verify.unannounced_pass",
                       {"prior_status": status, "score": report.score})
        return finish(ctx, task, "done_unannounced",
                      answer=best_answer, steps=steps)
    return finish(ctx, task, status, answer=best_answer, steps=steps)


def _verify(ctx, task, artifact: str):
    r = ctx.mesh.verify(task, artifact)
    return r.passed, r


def _envelope(ctx, backend, tail: str, allow_final: bool = True) -> tuple[str, dict | None]:
    if backend.cfg.needs_action_envelope:
        if getattr(backend.cfg, "action_protocol", "json") == "tags":
            return tail + "\n" + ACTION_TAG_HINT, None
        if ctx.config.mechanisms.constrained_calls and backend.cfg.model_server == "ollama":
            hint = ACTION_SCHEMA_HINT if ctx.code_as_action else ACTION_SCHEMA_HINT_NO_CODE
            if not allow_final:
                hint += chr(10) + READ_FIRST_NOTE
            return tail + "\n" + hint, build_action_schema(ctx.tools, ctx.code_as_action, allow_final)
        return tail + "\n" + ACTION_SCHEMA_HINT, ACTION_SCHEMA
    return tail, None


FLUSH_NOTE = """\
<note>
The older observations above are about to be compacted: they will be replaced
by references you can expand. If a fact in them must outlive this task,
persist it now with ONE memory_write action; it will be in your context from
the next step. If nothing needs keeping, reply with the single word NOTHING.
</note>"""


def _flush_memory(ctx, task, builder, prefix: str, entries, step: int, plan):
    if getattr(ctx.config, "isolate_retrieval", False):
        return [], {"flush": "skipped", "why": "tier 1 is disabled under isolation"}
    try:
        exposure_check(ctx.tool_policy(task.id), "memory_write")
    except ToolNotExposed as e:
        return [], {"flush": "skipped", "why": str(e)}

    backend = ctx.router.choose("step", task, step)
    tail, schema = _envelope(ctx, backend, render_tail(entries) + "\n\n" + FLUSH_NOTE)
    ctx.log.append(task.id, "model.called",
                   {"backend": backend.name, "purpose": "memory_flush",
                    "step": step, "prefix_fp": builder.fingerprint()})
    resp = ctx.call_model(task, backend, "memory_flush",
                          prefix=prefix, tail=tail, tools=ctx.tools.schemas(),
                          max_tokens=backend.default_max_tokens(),
                          temperature=0.0, schema=schema,
                          on_chunk=stream.token_sink(task.id))
    ctx.log.append(task.id, "model.returned",
                   {"backend": backend.name, "purpose": "memory_flush",
                    "output_tokens": resp.usage.output_tokens})

    parsed = parse_action(resp.text)
    if parsed.kind != "action" or parsed.tool != "memory_write":
        return [], {"flush": "nothing"}

    tool = ctx.tools.get("memory_write")
    d = gate_tool_call(ctx, task, tool, parsed.args, phase="execute")
    if d.verdict == "needs_approval":
        d = _adjudicate(ctx, task, tool, parsed.args, d, step, plan, park=False)
    if d.verdict != "allow":
        ctx.log.append(task.id, "memory.flush_refused",
                       {"step": step, "reason": d.reason,
                        "preview": render_preview(tool, parsed.args)})
        return [LedgerEntry.observation(
            "A fact offered to persistent memory before compaction was "
            f"REFUSED: {d.reason}. It was not stored, and the observations it "
            "came from are now references only.",
            taint=Taint.TRUSTED, label="memory_flush")], \
            {"flush": "refused", "why": d.reason}

    before = ctx.memory.fingerprint()
    entry = _perform(ctx, task, tool, "memory_write", parsed.args, step)
    return [entry], {"flush": "written" if ctx.memory.fingerprint() != before
                     else "failed"}


def _session_boundary(ctx, task, entries, window, step, plan, skills, builder,
                      prefix):
    flushed, record = _flush_memory(ctx, task, builder, prefix, entries, step, plan)
    entries = compact(entries, target_tokens=max(
        0, int(window * 0.20) - count_tokens(prefix))) + flushed
    tombstoned = sum(1 for e in entries if e.kind == "tombstone")
    seed = write_artifact(
        ctx.conn, task.id, "session_seed", render_tail(entries),
        join_taint(*(e.taint for e in entries)), {"step": step})
    rot = ctx.rotate_session(task.id, seed, {**record, "step": step,
                                             "tombstoned": tombstoned})
    guard = PrefixGuard() if ctx.config.mechanisms.prefix_stability else None
    builder = PrefixBuilder(
        system_contract(ctx.code_as_action),
        skills, json.loads(task.contract.model_dump_json()),
        tool_manual=ctx.tools.manual(), memory=rot["memory"],
        project_notes=ctx.freeze_project_notes(task))
    if guard is not None:
        guard.check(builder, task.id)
    return entries, rot["memory"], guard, builder, builder.build()


def _seed_record(ctx, task, entries: list[LedgerEntry], kind: str = "park_seed") -> dict:
    payload = json.dumps(
        [{"kind": e.kind, "content": e.content, "artifact_id": e.artifact_id,
          "taint": e.taint.value, "pinned": e.pinned, "label": e.label,
          "exact_strings": list(e.exact_strings)} for e in entries],
        sort_keys=True)
    aid = write_artifact(ctx.conn, task.id, kind, payload,
                         join_taint(*(e.taint for e in entries)),
                         {"entries": len(entries)})
    return {"artifact": aid, "sha256": sha256_hex(payload), "entries": len(entries)}


def _write_park_seed(ctx, task, entries: list[LedgerEntry]) -> None:
    ctx.log.append(task.id, "task.park_seed", _seed_record(ctx, task, entries))


def _changes_something(ctx, parsed) -> bool:
    tool = ctx.tools.get("run_code" if parsed.kind == "code" else parsed.tool)
    return tool is not None and tool.side_effecting


def _checkpoint(ctx, task, step: int, entries: list[LedgerEntry]) -> None:
    if not ctx.config.mechanisms.shadow_checkpoints:
        return
    from .shadow import ShadowRepo, ShadowUnavailable

    try:
        sha = ShadowRepo.for_task(ctx, task).checkpoint(f"{task.id} step {step}")
    except (ShadowUnavailable, OSError, subprocess.TimeoutExpired) as e:
        ctx.log.append(task.id, "checkpoint.skipped", {"step": step, "why": str(e)[:200]})
        return
    ctx.log.append(task.id, "checkpoint.shadow",
                   {"step": step, "sha": sha, **_seed_record(ctx, task, entries, "rewind_seed")})


_MAX_LISTED = 100


def _restore_park_seed(ctx, task, fresh: list[LedgerEntry]) -> list[LedgerEntry]:
    out: list[LedgerEntry] = []
    restored = 0
    row = ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='task.park_seed' "
        "ORDER BY seq DESC LIMIT 1", (task.id,)).fetchone()
    seed = json.loads(row[0]) if row else None
    if seed is not None:
        art = read_artifact(ctx.conn, seed["artifact"])
        if art is None or sha256_hex(art["content"]) != seed["sha256"]:
            raise IntegrityError(
                f"park seed for {task.id} does not match the hash on its chain; "
                "refusing to show the model a tail nobody can vouch for")
        already = {e.content for e in fresh}
        tracker = ctx.taint_tracker(task.id)
        for d in json.loads(art["content"]):
            if d["kind"] == "note" and d["content"] in already:
                continue
            e = LedgerEntry(kind=d["kind"], content=d["content"],
                            artifact_id=d.get("artifact_id"), taint=Taint(d["taint"]),
                            pinned=bool(d.get("pinned")), label=d.get("label", ""),
                            exact_strings=list(d.get("exact_strings") or []))
            tracker.observe(e.content, e.taint)
            out.append(e)
            restored += 1

    ids = ctx.conn.execute(
        """SELECT json_extract(e.payload_json, '$.id'),
                  json_extract(a.origin_json, '$.tool')
           FROM events e JOIN artifacts a ON a.id = json_extract(e.payload_json, '$.id')
           WHERE e.task_id = ? AND e.type = 'artifact.written' ORDER BY e.seq""",
        (task.id,)).fetchall()
    shown = ids[-_MAX_LISTED:]
    if shown:
        more = (f"\n  ({len(ids) - len(shown)} earlier not listed)"
                if len(ids) > len(shown) else "")
        out.append(LedgerEntry.note(
            f"This task was resumed. It made {len(ids)} observation(s) before; "
            "each can be read back in full with expand_artifact(id):\n"
            + "\n".join(f"  {aid}  {tool or '?'}" for aid, tool in shown) + more))
    if seed is not None or shown:
        ctx.log.append(task.id, "task.seed_restored",
                       {"artifact": seed["artifact"] if seed else None,
                        "entries": restored, "ids_listed": len(shown)})
    return out


def _release_decided(ctx, task) -> list[LedgerEntry]:
    rows = ctx.conn.execute(
        """SELECT id, step_id, tool, args_json, status FROM approvals
           WHERE task_id=? AND status IN ('approved', 'denied')
             AND consumed_at IS NULL
           ORDER BY created_at, rowid""",
        (task.id,),
    ).fetchall()
    out: list[LedgerEntry] = []
    for approval_id, step_id, tool_name, args_json, status in rows:
        args = json.loads(args_json)
        tool = ctx.tools.get(tool_name)
        if status == "denied":
            preview = render_preview(tool, args) if tool else tool_name
            entry = LedgerEntry.observation(
                f"A human DENIED {preview}. Do not attempt the same outcome "
                "by another route.",
                taint=Taint.TRUSTED, label="human")
            outcome = "denied"
        else:
            refusal = None
            if tool is None:
                refusal = f"{tool_name} is no longer registered"
            else:
                try:
                    exposure_check(ctx.tool_policy(task.id), tool_name)
                    d = gate_tool_call(ctx, task, tool, args, phase="execute")
                    if d.verdict == "deny":
                        refusal = d.reason
                except ToolNotExposed as e:
                    refusal = str(e)
            if refusal is not None:
                entry = LedgerEntry.observation(
                    f"A human approved {tool_name}, but it is no longer "
                    f"permitted: {refusal}",
                    taint=Taint.TRUSTED, label="human")
                outcome = "void"
            else:
                entry = _perform(ctx, task, tool, tool_name, args,
                                 int(step_id or 0))
                outcome = "executed"
        ctx.conn.execute("UPDATE approvals SET consumed_at=? WHERE id=?",
                         (time.time(), approval_id))
        ctx.conn.commit()
        ctx.log.append(task.id, "approval.consumed",
                       {"approval_id": approval_id, "tool": tool_name,
                        "decision": status, "outcome": outcome})
        out.append(entry)
    return out


def execute_action(ctx, task, parsed: ParsedAction, step: int, plan=None) -> LedgerEntry:
    tool_name = "run_code" if parsed.kind == "code" else parsed.tool
    args = parsed.args
    tool = ctx.tools.get(tool_name)
    if tool is None:
        return LedgerEntry.observation(
            f"no such tool {tool_name!r}. Available: {', '.join(ctx.tools.names())}",
            taint=Taint.TRUSTED,
            label="registry",
        )

    try:
        exposure_check(ctx.tool_policy(task.id), tool_name)
    except ToolNotExposed as e:
        ctx.log.append(task.id, "tool.not_exposed",
                       {"tool": tool_name, "step": step,
                        "policy": ctx.tool_policy(task.id).describe()})
        return LedgerEntry.observation(
            f"REFUSED: {e}", taint=Taint.TRUSTED, label="exposure")

    decision = gate_tool_call(ctx, task, tool, args, phase=ctx.phase)

    if decision.verdict == "deny":
        ctx.log.append(
            task.id, "human.denied", {"tool": tool_name, "reason": decision.reason}
        )
        return LedgerEntry.observation(
            f"DENIED by the capability gate: {decision.reason}\n"
            "This is a rule, not a judgment call, and it will not change if you "
            "rephrase. Do not attempt the same outcome by another route.",
            taint=Taint.TRUSTED,
            label="gate",
        )

    if decision.verdict == "needs_approval":
        decision = _adjudicate(ctx, task, tool, args, decision, step, plan)
        if decision.verdict == "deny":
            return LedgerEntry.observation(
                decision.reason, taint=Taint.TRUSTED, label="review"
            )

    if ctx.config.hooks.pre_tool:
        from .hooks import run_hooks

        allowed, why = run_hooks(ctx, task, "pre_tool", tool_name, args=args)
        if not allowed:
            ctx.log.append(task.id, "hook.denied", {"tool": tool_name, "step": step, "why": why[:300]})
            return LedgerEntry.observation(
                f"hook refused {tool_name} (a check the user configured): {why}" + chr(10)
                + "This is a rule, not a judgment call.", taint=Taint.UNTRUSTED, label="hook")

    obs = _perform(ctx, task, tool, tool_name, args, step)
    if ctx.config.hooks.post_tool:
        from .hooks import run_hooks

        _, text = run_hooks(ctx, task, "post_tool", tool_name, args=args, result=obs.content[:2000])
        if text:
            obs.content += chr(10) + chr(10) + "<hook_output taint=untrusted>" + chr(10) + text + chr(10) + "</hook_output>"
            obs.taint = join_taint(obs.taint, Taint.UNTRUSTED)
            obs.n_tokens = count_tokens(obs.content)
    return obs


FILE_EDITS = ("write_file", "edit_file", "apply_patch")


def _file_bytes(ctx, args: dict):
    from .tools import _resolve

    try:
        return _resolve(ctx, str(args["path"])).read_bytes()
    except (OSError, KeyError):
        return None


def _perform(ctx, task, tool, tool_name: str, args: dict, step: int) -> LedgerEntry:
    key = idempotency_key(task.id, step + rewind_epoch(ctx.conn, task.id), tool_name, args)
    intent = {"tool": tool_name, "step": step, "key": key,
              "preview": render_preview(tool, args)}
    if tool_name == "run_code":
        intent.update(_code_record(ctx, task, str(args.get("code", ""))))
    ctx.log.append(task.id, "tool.intent", intent)

    t0 = time.time()
    before = _file_bytes(ctx, args) if tool_name in FILE_EDITS else None
    try:
        if tool_name == "run_code":
            from .codeact import run_code_action

            result = run_code_action(
                ctx, task, args.get("code", ""),
                timeout_s=int(args.get("timeout_s", 30)), step=step,
            )
        elif tool.side_effecting:
            result = execute_once(
                ctx.conn, key, tool_name, args, lambda: tool.fn(ctx, task, **args)
            )
        else:
            result = tool.fn(ctx, task, **args)
    except TypeError as e:
        return LedgerEntry.observation(
            f"{tool_name} rejected those arguments: {e}", taint=Taint.TRUSTED, label="tool"
        )
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if (tool_name in FILE_EDITS and ctx.config.mechanisms.diagnostics
            and isinstance(result, dict) and result.get("ok") and result.get("path")):
        from .diagnostics import diagnose

        found = diagnose(result["path"])
        if found:
            result["diagnostics"] = found
    touched = result.pop("touched", None) if isinstance(result, dict) else None
    receipt = {"tool": tool_name, "key": key, "ok": bool(result.get("ok", True)),
               "wall_ms": int((time.time() - t0) * 1000)}
    if isinstance(result, dict):
        changed = bool(result.get("changed_files")) or (
            tool_name in FILE_EDITS and bool(result.get("ok")) and _file_bytes(ctx, args) != before)
        if changed:
            receipt["changed"] = True
        if tool_name == "run_command" and result.get("verification"):
            receipt["verify"] = True
    if tool_name == "run_code":
        receipt.update(_touched_record(touched))
        receipt["limits"] = list(result.get("notes") or []) if isinstance(result, dict) else []
    ctx.log.append(task.id, "tool.receipt", receipt)

    text = _render_result(result)
    taint = tool.output_taint
    origin = {"tool": tool_name,
              "args": {k: str(v)[:200] for k, v in args.items()}}
    if isinstance(result, dict) and isinstance(result.get("provenance"), dict):
        origin["provenance"] = result["provenance"]
    aid = write_artifact(
        ctx.conn, task.id, "tool_output", text, taint, origin,
    )
    ctx.log.append(task.id, "artifact.written",
                   {"id": aid, "kind": "tool_output", "taint": taint.value, "key": key})
    ctx.taint_tracker(task.id).trust_paths(args)
    ctx.taint_tracker(task.id).observe(text, taint)

    rendered, via = condense_observation(ctx, task, text, taint, aid)
    label = f"{tool_name} via {via}" if via else tool_name
    return LedgerEntry.observation(rendered, artifact_id=aid, taint=taint, label=label)


def _code_record(ctx, task, code: str) -> dict:
    from ..kernel.events import sha256_hex

    taint = ctx.taint_tracker(task.id).args_taint({"code": code})
    aid = write_artifact(ctx.conn, task.id, "code", code, taint,
                         {"tool": "run_code", "role": "source"})
    return {"code_sha256": sha256_hex(code), "code_artifact": aid,
            "code_chars": len(code)}


def _touched_record(touched: dict | None) -> dict:
    if touched is None:
        return {"code_touched": None}
    return {"code_touched": touched["entries"],
            "code_touched_dropped": touched["dropped"],
            "code_denied": touched["denied"]}


def _inline_cap(ctx) -> int:
    b = ctx.backends.get("cloud")
    return b.cfg.inline_observation_tokens if b else 400


def condense_observation(ctx, task, text: str, taint: Taint, aid: str) -> tuple[str, str | None]:
    n = count_tokens(text)
    if n <= ctx.config.compress_observations_over_tokens:
        return project(ctx.conn, aid, mode="summary", max_tokens=_inline_cap(ctx)), None

    backend = ctx.router.choose("compress_tool_output", task)
    if backend.cfg.trust_tier != "local":
        return project(ctx.conn, aid, mode="summary", max_tokens=_inline_cap(ctx)), None

    quarantined = taint == Taint.UNTRUSTED
    prefix = QUARANTINE_PREFIX if quarantined else COMPRESS_PREFIX
    question = f"What in this output bears on: {task.contract.goal}"

    op = f"condense:{'quarantine' if quarantined else 'compress'}:{question}"
    cache_hit = {"v": False}

    def _summarize() -> dict:
        resp = ctx.call_model(
            task, backend, "compress_tool_output",
            prefix=prefix,
            tail=f"<question>{question}</question>\n<data>\n{text[:60000]}\n</data>",
            tools=None,
            max_tokens=512,
            temperature=0.0,
        )
        return {"text": resp.text}

    try:
        if ctx.config.mechanisms.semantic_cache:
            value, hit = cached_op(ctx.conn, op, text, taint, _summarize)
        else:
            value, hit = _summarize(), False
        cache_hit["v"] = hit
        summary = value.get("text", "")
    except Exception as e:  # noqa: BLE001
        ctx.log.append(
            task.id, "model.returned",
            {"backend": backend.name, "purpose": "compress_tool_output",
             "error": f"{type(e).__name__}"},
        )
        return project(ctx.conn, aid, mode="summary", max_tokens=_inline_cap(ctx)), None

    note = "quarantined summary" if quarantined else "compressed"
    if cache_hit["v"]:
        note += ", cached"
    body = (
        f"[{note} by {backend.name}; {n:,} tokens of raw output kept in "
        f'artifact {aid} · expand_artifact("{aid}")]\n{summary.strip()}'
    )
    return body, backend.name


def _adjudicate(ctx, task, tool, args, decision: GateDecision, step: int, plan,
                park: bool = True) -> GateDecision:
    from ..plan.review import ReviewRequest, auto_review, tier_of

    tracker = ctx.taint_tracker(task.id)
    arg_taint = tracker.args_taint(args)
    tier = tier_of(ctx, tool.name)
    human_only = tier == "trusted" or tier in ctx.config.autonomy.human_only_tiers

    if is_unattended(task.origin):
        ctx.log.append(task.id, "review.skipped",
                       {"tool": tool.name, "step": step, "origin": task.origin,
                        "reason": f"{task.origin} task: nobody is watching, so "
                                  "it parks for a human"})
    elif ctx.config.autonomy.auto_review and not human_only:
        ctx.log.append(
            task.id,
            "review.requested",
            {"tool": tool.name, "step": step, "arg_taint": arg_taint.value},
        )
        req = ReviewRequest(
            task_id=task.id,
            step_id=str(step),
            tool=tool.name,
            args=args,
            arg_taint=arg_taint,
            compact_transcript=ctx.compact_transcript(task.id),
            plan_step=plan.steps[0] if plan and plan.steps else None,
            artifact_scope=list(task.contract.artifact_scope),
        )
        d = auto_review(ctx, req, task=task)
        if d.verdict == "approve":
            return GateDecision.allow_(d.rationale)
        if d.verdict == "deny":
            return GateDecision.deny(d.to_agent())

    if not park:
        return GateDecision.deny(
            f"{tool.name} needs a human, and this call cannot wait for one")
    approval_id = ctx.request_approval(task, tool, args, decision.reason, step)
    raise TaskPaused(approval_id, f"approval required for {tool.name}")


def _render_result(result: dict) -> str:
    if "content" in result and isinstance(result["content"], str):
        head = {k: v for k, v in result.items() if k != "content"}
        return f"{json.dumps(head)}\n---\n{result['content']}"
    return json.dumps(result, indent=2, default=str)


def finish(
    ctx, task, status: str, answer: Any = None, steps: int = 0
) -> TaskResult:
    mapping = {
        "done": TaskStatus.DONE,
        "done_unannounced": TaskStatus.DONE,
        "budget_exhausted": TaskStatus.FAILED,
        "max_steps": TaskStatus.FAILED,
        "plateau": TaskStatus.FAILED,
        "needs_replan": TaskStatus.PAUSED,
        "failed": TaskStatus.FAILED,
        "crashed": TaskStatus.FAILED,
        "model_stalled": TaskStatus.FAILED,
        "stuck": TaskStatus.FAILED,
    }
    set_status(ctx.conn, ctx.log, task, mapping.get(status, TaskStatus.FAILED), status)
    if mapping.get(status, TaskStatus.FAILED) != TaskStatus.PAUSED:
        ctx.end_session(task.id, status)
    checkpoint(ctx.log, ctx.anchor, task.id)

    ctx.record_skill_invocations(
        task.id, success=status in ("done", "done_unannounced"))
    if answer is not None:
        write_artifact(
            ctx.conn,
            task.id,
            "answer",
            answer if isinstance(answer, str) else json.dumps(answer, indent=2),
            Taint.TRUSTED,
            {"source": "final"},
        )
    return TaskResult(task.id, status, answer=answer, steps=steps, reason=status)
