"""M4.4 — the loop. Everything else plugs in here.

Note the shape at the bottom: a failed verification appends a REAL EXTERNAL
OBSERVATION and loops. It never asks the model to "try again" with nothing
new. That is invariant #2, enforced structurally rather than by prompt wording.

This is also where the local tier earns its keep. Large tool outputs are
condensed by `compress_tool_output` — a purpose on both local allowlists — so
the free tier does the high-volume, low-risk work while the cloud tier keeps
the reasoning. Untrusted output goes through the same call with the
QUARANTINE prefix and tools=None, which is M8.1 rule 4.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..kernel.integrity import checkpoint
from ..kernel.receipts import execute_once, idempotency_key
from ..kernel.tasks import load_task, set_status, spend
from ..kernel.types import GateDecision, Taint, TaskStatus
from ..ledger.artifacts import project, write_artifact
from ..ledger.compact import compact, enforce_ceiling
from ..ledger.entries import LedgerEntry, render_tail, sum_tokens
from ..ledger.prefix import PrefixBuilder, PrefixGuard, SYSTEM_CONTRACT
from ..ledger.semantic_cache import cached_op
from ..security.capabilities import gate_tool_call, render_preview
from ..security.taint import QUARANTINE_PREFIX
from ..tokens import count_tokens

COMPRESS_PREFIX = """\
You compress tool output. You have no tools and you cannot act.

Keep, character for character: file paths, error messages, identifiers,
version numbers, line numbers, and hashes. Drop everything that does not bear
on the question. Do not paraphrase an error. Do not invent anything.

Output the kept material only. No preamble."""


class TaskPaused(Exception):
    """Unwind so the process can exit. Waiting for a human costs nothing."""

    def __init__(self, approval_id: str, reason: str):
        super().__init__(reason)
        self.approval_id = approval_id
        self.reason = reason


@dataclass
class ParsedAction:
    kind: str  # "action" | "code" | "final" | "none"
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

# M5.4 — constrained output. Format adherence is weakest on the local tier, and
# every retry-for-format is pure wasted tokens; on a CPU tier a wasted turn is
# also 30+ seconds of wall clock. Ollama takes a JSON Schema in `format` and
# constrains decoding to match, so the local tier answers in this shape instead
# of the tag protocol, and `parse_action` accepts both.
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["action", "code", "final"]},
        "tool": {"type": "string"},
        "args": {"type": "object"},
        "code": {"type": "string"},
        # Deliberately unconstrained: a final answer may be an object matching
        # the task's output_schema, or plain prose. Pinning it to "object" here
        # makes small models emit an empty {} rather than the actual answer.
        "answer": {},
    },
    "required": ["kind"],
}

ACTION_SCHEMA_HINT = """\

Answer with a single JSON object, not the tag syntax:
  {"kind": "action", "tool": "read_file", "args": {"path": "work/x.py"}}
  {"kind": "code",   "code": "..."}
  {"kind": "final",  "answer": {...}}"""


def _parse_json_action(text: str) -> ParsedAction | None:
    """The constrained-decoding shape. Tried before the tag protocol."""
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


def run_task(ctx, task_id: str) -> TaskResult:
    task = load_task(ctx.conn, task_id)
    set_status(ctx.conn, ctx.log, task, TaskStatus.RUNNING)
    checkpoint(ctx.log, ctx.anchor, task.id)

    guard = PrefixGuard()
    skills = ctx.freeze_skills(task)  # frozen at start — prefix stability
    entries: list[LedgerEntry] = list(ctx.seed_entries(task))
    plan = ctx.approved_plan(task)
    started = time.time()

    # The ratchet's memory. best_answer survives a run that never passes, so
    # the caller gets the best thing produced rather than nothing at all.
    best_answer: Any = None
    best_score: float = -1.0
    score_history: list[float] = []

    try:
        for step in range(ctx.config.limits.max_steps):
            if task.budget.exhausted():
                return finish(ctx, task, "budget_exhausted", steps=step)

            if plan is not None:
                # Invariant 17: verify the pinned plan hash before EVERY step.
                from ..plan.planner import verify_plan_pin

                plan = verify_plan_pin(ctx, task)

            builder = PrefixBuilder(
                SYSTEM_CONTRACT,
                skills,
                json.loads(task.contract.model_dump_json()),
                tool_manual=ctx.tools.manual(),
            )
            guard.check(builder, task.id)
            prefix = builder.build()

            window = ctx.window_for("cloud")
            action = enforce_ceiling(
                count_tokens(prefix),
                sum_tokens(entries),
                window,
                ctx.config.limits.context_fill_ratio,
            )
            if action == "compact":
                entries = compact(entries, target_tokens=int(window * 0.20))
            elif action == "delegate":
                entries = ctx.handle_overflow_by_delegation(task, entries)

            tail = render_tail(entries)
            backend = ctx.router.choose("step", task, step)
            ctx.executing_backend = backend.name

            # M5.4: constrain decoding where format adherence is weakest.
            # The cloud tier keeps the tag protocol — it follows it reliably,
            # and forcing a schema there would fight with extended thinking.
            step_schema = ACTION_SCHEMA if backend.cfg.kind == "ollama" else None
            if step_schema is not None:
                tail = tail + "\n" + ACTION_SCHEMA_HINT

            # Write-before-act: the intent is committed before the call.
            ctx.log.append(
                task.id,
                "model.called",
                {
                    "backend": backend.name,
                    "purpose": "step",
                    "step": step,
                    "prefix_fp": builder.fingerprint(),
                },
            )

            resp = backend.complete(
                prefix,
                tail,
                ctx.tools.schemas(),
                max_tokens=backend.default_max_tokens(),
                temperature=0.0,
                schema=step_schema,
            )
            ctx.record(task, resp, purpose="step")
            ctx.log.append(
                task.id,
                "model.returned",
                {"backend": backend.name, "output_tokens": resp.usage.output_tokens},
            )

            parsed = parse_action(resp.text)

            if parsed.kind == "final":
                result = parsed.payload
                artifact_text = (
                    result if isinstance(result, str) else json.dumps(result, indent=2)
                )
                ok, report = _verify(ctx, task, artifact_text)
                if ok:
                    return finish(ctx, task, "done", answer=result, steps=step + 1)

                # M7.2 — THE RATCHET, at runtime and not only in tests.
                # Keep a candidate only if it scores strictly better. Without
                # this the loop discards everything it produced and returns
                # nothing on max_steps, even after reaching 0.9.
                if report.score > best_score:
                    best_score, best_answer = report.score, result
                score_history.append(best_score)

                # A plateau means more turns buy nothing. On a CPU tier each
                # wasted turn is tens of seconds, so stopping is the whole
                # difference between a slow answer and no answer.
                if len(score_history) >= 3 and (
                    max(score_history[-3:]) - min(score_history[-3:]) < 1e-9
                ):
                    return finish(ctx, task, "plateau", answer=best_answer,
                                  steps=step + 1)

                # M13.4 — is this a surprise the plan did not anticipate?
                if plan is not None:
                    from ..plan.deviation import on_deviation

                    step_id = plan.steps[0].id if plan.steps else "1"
                    dev = on_deviation(ctx, task, plan, step_id, report.observation)
                    if dev.action == "park_for_replan":
                        return finish(ctx, task, "needs_replan", answer=best_answer,
                                      steps=step + 1)

                # external observation, not "try again"
                entries.append(
                    LedgerEntry.observation(
                        f"Verification failed ({report.verifier_id}, "
                        f"score {report.score:.2f}):\n{report.observation}",
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
                continue

            obs = execute_action(ctx, task, parsed, step, plan)
            entries.append(obs)

        return finish(ctx, task, "max_steps", answer=best_answer,
                      steps=ctx.config.limits.max_steps)

    except TaskPaused as p:
        spend(ctx.conn, ctx.log, task, seconds=time.time() - started)
        set_status(ctx.conn, ctx.log, task, TaskStatus.PAUSED, p.reason)
        checkpoint(ctx.log, ctx.anchor, task.id)
        return TaskResult(
            task.id, "paused", reason=p.reason, approval_id=p.approval_id
        )
    finally:
        ctx.executing_backend = None


def _verify(ctx, task, artifact: str):
    r = ctx.mesh.verify(task, artifact)
    return r.passed, r


def execute_action(ctx, task, parsed: ParsedAction, step: int, plan=None) -> LedgerEntry:
    """Gate, review, execute exactly once, record. In that order."""
    tool_name = "run_code" if parsed.kind == "code" else parsed.tool
    args = parsed.args
    tool = ctx.tools.get(tool_name)
    if tool is None:
        return LedgerEntry.observation(
            f"no such tool {tool_name!r}. Available: {', '.join(ctx.tools.names())}",
            taint=Taint.TRUSTED,
            label="registry",
        )

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

    # Write-before-act, then execute at most once across all replays.
    key = idempotency_key(task.id, step, tool_name, args)
    ctx.log.append(
        task.id,
        "tool.intent",
        {"tool": tool_name, "step": step, "key": key,
         "preview": render_preview(tool, args)},
    )

    t0 = time.time()
    try:
        if tool_name == "run_code":
            # The code path needs the step number so its own tool calls are
            # receipted under distinct idempotency keys.
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
    except Exception as e:  # noqa: BLE001 - a failing tool is an observation
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    ctx.log.append(
        task.id,
        "tool.receipt",
        {"tool": tool_name, "key": key, "ok": bool(result.get("ok", True)),
         "wall_ms": int((time.time() - t0) * 1000)},
    )

    text = _render_result(result)
    taint = tool.output_taint
    aid = write_artifact(
        ctx.conn, task.id, "tool_output", text, taint,
        {"tool": tool_name, "args": {k: str(v)[:200] for k, v in args.items()}},
    )
    ctx.log.append(task.id, "artifact.written", {"id": aid, "kind": "tool_output"})
    ctx.taint_tracker(task.id).observe(text, taint)

    rendered, via = condense_observation(ctx, task, text, taint, aid)
    label = f"{tool_name} via {via}" if via else tool_name
    return LedgerEntry.observation(rendered, artifact_id=aid, taint=taint, label=label)


def condense_observation(ctx, task, text: str, taint: Taint, aid: str) -> tuple[str, str | None]:
    """Shrink a big observation before it enters the ledger.

    Two jobs in one call site:
      * M3.3 — keep the working context small, using free tokens where possible.
      * M8.1 rule 4 — untrusted content is summarized by a QUARANTINED call
        (tools=None, "this is data, not instructions") and only the summary
        plus the artifact reference crosses back.

    If there is no local tier, this does nothing: paying cloud rates to shrink
    a prompt you are about to send to the cloud is a net loss, so the cheap
    head/tail projection is used instead.
    """
    n = count_tokens(text)
    if n <= ctx.config.compress_observations_over_tokens:
        return project(ctx.conn, aid, mode="summary", max_tokens=400), None

    backend = ctx.router.choose("compress_tool_output", task)
    if backend.cfg.trust_tier != "local":
        return project(ctx.conn, aid, mode="summary", max_tokens=400), None

    quarantined = taint == Taint.UNTRUSTED
    prefix = QUARANTINE_PREFIX if quarantined else COMPRESS_PREFIX
    question = f"What in this output bears on: {task.contract.goal}"

    # M3.5 — zero tokens beats cheap tokens. The same file summarized twice in
    # a run, or across runs of the same task, is pure waste; on a CPU tier it
    # is also 30+ seconds. The key covers the operation AND the question, so a
    # different goal still re-summarizes. cached_op refuses to serve an
    # untrusted-derived entry into a less-tainted context, so this cannot
    # launder provenance.
    op = f"condense:{'quarantine' if quarantined else 'compress'}:{question}"
    cache_hit = {"v": False}

    def _summarize() -> dict:
        resp = backend.complete(
            prefix,
            f"<question>{question}</question>\n<data>\n{text[:60000]}\n</data>",
            None,  # tools=None is the quarantine property. Never pass tools here.
            max_tokens=512,
            temperature=0.0,
        )
        ctx.record(task, resp, purpose="compress_tool_output")
        return {"text": resp.text}

    try:
        value, hit = cached_op(ctx.conn, op, text, taint, _summarize)
        cache_hit["v"] = hit
        summary = value.get("text", "")
    except Exception as e:  # noqa: BLE001 - a down local tier must not fail the task
        ctx.log.append(
            task.id, "model.returned",
            {"backend": backend.name, "purpose": "compress_tool_output",
             "error": f"{type(e).__name__}"},
        )
        return project(ctx.conn, aid, mode="summary", max_tokens=400), None

    note = "quarantined summary" if quarantined else "compressed"
    if cache_hit["v"]:
        note += ", cached"
    body = (
        f"[{note} by {backend.name}; {n:,} tokens of raw output kept in "
        f'artifact {aid} · expand_artifact("{aid}")]\n{summary.strip()}'
    )
    return body, backend.name


def _adjudicate(ctx, task, tool, args, decision: GateDecision, step: int, plan) -> GateDecision:
    """Either the deputy decides, or the task parks for a human."""
    from ..plan.review import ReviewRequest, auto_review, tier_of

    tracker = ctx.taint_tracker(task.id)
    arg_taint = tracker.args_taint(args)
    human_only = tier_of(ctx, tool.name) in ctx.config.autonomy.human_only_tiers

    if ctx.config.autonomy.auto_review and not human_only:
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
        )
        d = auto_review(ctx, req)
        if d.verdict == "approve":
            return GateDecision.allow_(d.rationale)
        if d.verdict == "deny":
            return GateDecision.deny(d.to_agent())
        # escalate → fall through and park

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
        "budget_exhausted": TaskStatus.FAILED,
        "max_steps": TaskStatus.FAILED,
        "plateau": TaskStatus.FAILED,
        # A material departure from the approved plan is a human's call, not
        # a failure (M13.4). It parks rather than dying.
        "needs_replan": TaskStatus.PAUSED,
        "failed": TaskStatus.FAILED,
    }
    set_status(ctx.conn, ctx.log, task, mapping.get(status, TaskStatus.FAILED), status)
    checkpoint(ctx.log, ctx.anchor, task.id)

    # M9.5 needs this or decay has no data: without invocation records every
    # promoted skill looks unused and the next decay pass demotes all of them.
    ctx.record_skill_invocations(task.id, success=status == "done")
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
