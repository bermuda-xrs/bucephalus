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

from ..kernel.events import sha256_hex
from ..kernel.integrity import IntegrityError, checkpoint, resume
from ..kernel.receipts import execute_once, idempotency_key
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
from .. import stream

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


def _verified_state(ctx, task_id: str) -> dict:
    """What `resume` just verified: the chain head, and the anchor it was
    compared against - "none" for a task never checkpointed, "matched", or
    "behind" (anchored hash still in the chain; see `resume`)."""
    head = ctx.log.head(task_id)
    anchored = ctx.anchor.latest_chain(task_id)
    state = "none" if not anchored else ("matched" if anchored == head else "behind")
    return {"head": head[:16] if head else None, "anchor": state}


def run_task(ctx, task_id: str) -> TaskResult:
    task = load_task(ctx.conn, task_id)

    # D20 — THE VERIFICATION DOOR. Every path by which a task's execution
    # continues comes through here, so this is where the chain is checked.
    #
    # `integrity.resume()` is M1.4/M1.5 and was INERT: written, tested, and
    # imported by nothing, so a paused task resumed without its chain ever
    # being verified. It is called here rather than at the three continuation
    # call sites (`alpha run`, and the control plane's two) because a door
    # each caller must remember to walk through is a door someone forgets —
    # and Phase 8 (a channel reviving a parked task) and Phase 11 (a
    # scheduled wake) would each have added another caller to remember.
    #
    # BEFORE `set_status`, deliberately: the chain is verified as it stands,
    # not after this run has already appended to it.
    #
    # A FRESH task passes trivially — an empty chain verifies and has no
    # anchor — so there is no fresh-versus-resumed branch to get wrong. That
    # absence is the point: a check that only runs on the resumed path is a
    # check nobody exercises until the day it matters.
    try:
        resume(ctx.conn, task.id, ctx.log, ctx.anchor)
    except IntegrityError:
        # Same response as a break found mid-run: halt, and do not sweep. A
        # broken chain destroys the ability to VERIFY, so a pass graded from
        # the workspace would rest on evidence that failed its own check.
        # Distinct from an anchor merely BEHIND, which `resume` allows
        # through — that is a task which never checkpointed, and refusing it
        # would reintroduce the denial of service D17 removed.
        set_status(ctx.conn, ctx.log, task, TaskStatus.FAILED, "integrity_halt")
        raise
    # A3-1: `resume` passes in silence, so a real run's log could show that it
    # did not FAIL and never that it RAN - FINDINGS 1.25 could not place it in
    # production for exactly that reason. Recorded after the check, on the
    # chain it checked.
    ctx.log.append(task.id, "integrity.verified", _verified_state(ctx, task.id))

    set_status(ctx.conn, ctx.log, task, TaskStatus.RUNNING)
    checkpoint(ctx.log, ctx.anchor, task.id)

    # G1's prefix-stability arm. `stable` decides whether skills and tier-1
    # memory are SNAPSHOT here once or re-read live at every step below. The
    # guard is armed only alongside the snapshots, because the guard detects
    # stability and the snapshots create it: disarming the guard on its own
    # would change no prompt byte and the arm would measure nothing at all -
    # DR2's decoy branch, in the measurement that most needs not to have one.
    stable = ctx.config.mechanisms.prefix_stability
    guard = PrefixGuard() if stable else None
    try:
        skills = ctx.freeze_skills(task)  # frozen at start — prefix stability
    except SkillSetChanged:
        # A resumed attempt whose recorded skills no longer load with the same
        # bytes. Under a different set it would be a different arm.
        set_status(ctx.conn, ctx.log, task, TaskStatus.FAILED, "skill_set_changed")
        raise
    memory = ctx.freeze_memory(task.id)   # same reason, same moment
    exe = ctx.backends.get("cloud")
    cal = calibration(ctx.conn, "cloud", exe.cfg.model) if exe else Calibration()
    ctx.log.append(task.id, "tokens.calibration",
                   {"backend": "cloud", "model": exe.cfg.model if exe else None,
                    "n": cal.n, "slope": round(cal.slope, 4),
                    "intercept": round(cal.intercept, 1), "fitted": cal.fitted})
    entries: list[LedgerEntry] = list(ctx.seed_entries(task))
    plan = ctx.approved_plan(task)
    started = time.time()

    # The ratchet's memory. best_answer survives a run that never passes, so
    # the caller gets the best thing produced rather than nothing at all.
    best_answer: Any = None
    best_score: float = -1.0
    score_history: list[float] = []
    charged_s = 0.0          # wall clock already charged to the budget

    try:
        # Invariant 17 covers a RELEASED action too - it is the rest of the
        # step that parked. FOUND BY THE PART 3 REAL RUN: the decided write
        # executed before this process had verified the pin, because the pin
        # was checked only at the top of each step and the release comes
        # before the first. A plan edited while the task sat parked would still
        # have had its approved action carried out. Verified here, before
        # anything this run does, and again before every step below.
        if plan is not None:
            from ..plan.planner import verify_plan_pin

            plan = verify_plan_pin(ctx, task)
        # D28. What this task saw when it parked, then where the rest of its
        # history is - before the released decisions, which came after it.
        entries.extend(_restore_park_seed(ctx, task, entries))
        # Human decisions made while this task was parked, acted on BEFORE the
        # model gets a turn. See `_release_decided`.
        entries.extend(_release_decided(ctx, task))

        for step in range(ctx.config.limits.max_steps):
            # Charge WALL CLOCK, not summed model time. The budget is a real
            # deadline: sandbox execution and verification cost the operator
            # the same seconds a model call does.
            elapsed = time.time() - started
            if elapsed > charged_s:
                spend(ctx.conn, ctx.log, task, seconds=elapsed - charged_s)
                charged_s = elapsed
            if task.budget.exhausted():
                return _final_sweep(ctx, task, "budget_exhausted",
                                    best_answer, step)

            if plan is not None:
                # Invariant 17: verify the pinned plan hash before EVERY step.
                from ..plan.planner import verify_plan_pin

                plan = verify_plan_pin(ctx, task)

            if not stable:
                # The prefix-stability arm, where the cost is paid: skills and
                # tier-1 memory come back from their stores at every step, so
                # anything written mid-task moves the prefix and voids the
                # discount for everything after it. That is what the shipped
                # system's snapshots buy, and the only way to price it is to
                # let it happen.
                skills = ctx.freeze_skills(task)
                memory = ctx._snapshot_memory(task.id)  # noqa: SLF001 - live read
            builder = PrefixBuilder(
                system_contract(ctx.config.mechanisms.code_as_action),
                skills,
                json.loads(task.contract.model_dump_json()),
                tool_manual=ctx.tools.manual(),
                memory=memory,
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
            # The synthetic boundary. Applied AFTER the real ceiling is
            # computed, never instead of it, so the record shows both what the
            # fill actually was and that the crossing was forced. See
            # `Config.compaction_probe_step`: on this executor the real
            # ceiling is essentially never reached, so waiting for it measures
            # the model's reluctance to read rather than the mechanism.
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
                # The compaction arm. The ceiling is still MEASURED and the
                # crossing is on the chain - an arm that silently grew past
                # the window would be indistinguishable from one that never
                # reached it, and "nothing to compact" and "compaction off"
                # are different facts (DR4). Nothing is dropped, so the tail
                # keeps growing; a prompt the backend then refuses is a
                # BACKEND ERROR and is already excluded from task failures
                # rather than scored as one.
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

            # M5.4: constrain decoding where format adherence is weakest.
            # The cloud tier keeps the tag protocol — it follows it reliably,
            # and forcing a schema there would fight with extended thinking.
            # Action protocol is per backend and MEASURED, not assumed. The
            # tag form removes the stray-brace bug at source (no escaping),
            # but granite4.1:8b never emitted a <final> under it across 8
            # tasks, so it cannot be the universal default.
            # See BackendConfig.action_protocol.
            #
            # The gate is `needs_action_envelope`, a declared property, and
            # NOT `kind == "ollama"` as it was until phase 1. Two adapters can
            # now reach the same Ollama server running the same weights, and
            # under a class-name test the config switch this phase exists to
            # enable would silently stop sending the schema — same model, same
            # task, a different question asked. Same defect class as a profile
            # that is whatever config happened to be on disk.
            tail, step_schema = _envelope(backend, tail)
            # Write-before-act: the intent is committed before the call.
            ctx.log.append(
                task.id,
                "model.called",
                {
                    "backend": backend.name,
                    "purpose": "step",
                    "step": step,
                    "prefix_fp": builder.fingerprint(),
                    # D6: what the in-loop ESTIMATOR thinks this prompt is,
                    # beside what the server will report in `calls`. The
                    # ceiling budgets against the estimator, and its error
                    # was unmeasurable without a key until C62. Now any run
                    # measures it.
                    "est_input_tokens": count_tokens(prefix) + count_tokens(tail),
                },
            )

            # MEASURED 2026-09-07: time to first token is a roughly fixed
            # ~2.4s of prompt evaluation on this hardware, independent of how
            # much is generated - 63-71% of a short turn is silent even with
            # streaming on. So a surface needs to know the call STARTED, not
            # only that tokens eventually arrived, or it shows nothing for
            # most of the turn and looks hung.
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

                # External observation, not "try again" - and through
                # `feedback_for_model`, never `report.observation` directly.
                # This line fed the grader's answer key to the model: the
                # 2026-09-22 ablation's handbook task "reported" three ticket
                # IDs it never read, copied from "missing: 'INF-4471', ...".
                # Under measurement the grader is held out and this says only
                # that the answer failed (FINDINGS 1.33, invariant 121).
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
                continue

            obs = execute_action(ctx, task, parsed, step, plan)
            entries.append(obs)

        return _final_sweep(ctx, task, "max_steps", best_answer,
                            ctx.config.limits.max_steps)

    except IntegrityError:
        # S2: DELIBERATELY NOT SWEPT. The hash chain is what makes any record
        # of this task trustworthy; if it is broken, a pass graded from the
        # workspace is a claim resting on evidence that failed its own
        # integrity check. Halt and let a human look.
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
        # A STALLED MODEL CALL IS NOT A TASK OUTCOME, and this handler has to
        # sit ahead of the generic one or it never runs. It did not, at first:
        # the stall detector fired correctly and `run_task` swallowed it into
        # `crashed`, which the suite scores as a FAILURE. Two tasks in a Phase
        # 2A run were mis-scored that way before the run was aborted - the
        # exact defect the detector was added to prevent, defeated by being
        # caught one level too low.
        ctx.log.append(task.id, "task.model_stalled",
                       {"error": f"{type(e).__name__}: {e}",
                        "observed_tok_s": getattr(e, "observed_rate", None),
                        "median_tok_s": getattr(e, "median_rate", None)})
        spend(ctx.conn, ctx.log, task, seconds=time.time() - started)

        # Sweep FIRST. The work may already be complete - a stall can land on
        # a call made after the answer was written - and re-running would
        # discard a correct artifact. Worse, because generation here is not
        # byte-deterministic, a re-run can turn a pass into a fail, so
        # discarding a verified artifact is a correctness problem and not
        # merely wasted compute.
        swept = _final_sweep(ctx, task, "model_stalled", best_answer, 0)
        if swept.status in ("done", "done_unannounced"):
            return swept

        # The work was not done. Hand the stall to the caller, which re-runs
        # the task once and excludes it on a second stall.
        raise

    except Exception as e:  # noqa: BLE001 - see below
        # S2: an unhandled exception used to propagate with the task left
        # RUNNING forever and its artifact never graded. The work may well be
        # complete - the crash can come from anywhere in the loop - so the
        # artifact is swept, and the failure is recorded either way.
        ctx.log.append(task.id, "task.crashed",
                       {"error": f"{type(e).__name__}: {e}"})
        spend(ctx.conn, ctx.log, task, seconds=time.time() - started)
        return _final_sweep(ctx, task, "crashed", best_answer, 0)

    finally:
        ctx.executing_backend = None


def _final_sweep(ctx, task, status: str, best_answer, steps: int) -> TaskResult:
    """Grade what the task PRODUCED before giving up on it.

    Verification used to run only inside the `<final>` branch, so a task that
    wrote a correct `output/answer.json` and never announced completion was
    recorded as a failure and never graded at all.

    Measured: granite4.1:8b on E01 wrote 7672.598648385013 against an expected
    7672.6 - correct to eleven significant figures - then emitted no <final>
    across nine steps and was scored `budget_exhausted`. The suite was
    measuring whether the model says it is done, not whether the work is done.

    The contract says "write output/answer.json". `verify_program` grades
    files. So the artifact is graded on every terminal path.

    A pass found this way is recorded as `done_unannounced`, NOT as `done`:
    the work is correct and the pass rate should say so, but failing to
    terminate is a real behavioural deficiency and the report must be able to
    count it separately.
    """
    # A sweep grades a produced ARTIFACT. `always_pass` grades nothing, so
    # sweeping it would turn every budget exhaustion into a pass - handing out
    # exactly the free credit this change must not create.
    #
    # And a sweep with NO ANSWER grades only what is on disk. It used to grade
    # `json.dumps(best_answer or {})` - the string "{}" - and `execution` ran
    # that as Python and passed it, so write-function-002 scored
    # done_unannounced in every v5 arm having submitted nothing (FINDINGS
    # 1.34). An answer kind has nothing to judge; a workspace kind still does.
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


def _envelope(backend, tail: str) -> tuple[str, dict | None]:
    """The action protocol a backend needs, MEASURED per backend (see the step
    code in `run_task`). Shared with the flush so both calls ask one question."""
    if backend.cfg.needs_action_envelope:
        if getattr(backend.cfg, "action_protocol", "json") == "tags":
            return tail + "\n" + ACTION_TAG_HINT, None
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
    """9.4 - one chance to persist a fact before compaction deletes its source.

    A REFUSAL IS RECORDED, never dropped. Tier 1 refuses untrusted-derived text
    (Phase 3), so a model that tries to keep a fact copied from a web page is
    refused - correctly - and the fact is then gone, because its observation is
    about to become a reference. Silently, that is a fact that vanished at
    compression with no record of why. So a refusal writes
    `memory.flush_refused` on the chain AND puts an observation in the child's
    tail saying what was refused and why.

    NEVER PARKS. A flush that needed a human would park the task mid-boundary.
    It is one chance, not a conversation, so `needs_approval` without a
    deputy's approval is a refusal here.

    Skipped - visibly, in the boundary's event - where tier 1 cannot be
    written: under isolation it is disabled, and exposure may deny it (a
    channel task, a delegated child).
    """
    if getattr(ctx.config, "isolate_retrieval", False):
        return [], {"flush": "skipped", "why": "tier 1 is disabled under isolation"}
    try:
        exposure_check(ctx.tool_policy(task.id), "memory_write")
    except ToolNotExposed as e:
        return [], {"flush": "skipped", "why": str(e)}

    backend = ctx.router.choose("step", task, step)
    tail, schema = _envelope(backend, render_tail(entries) + "\n\n" + FLUSH_NOTE)
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
    """9.3/9.4 - THE SESSION BOUNDARY, at the compaction point that already existed.

    In order, each for a stated reason:
      1. FLUSH. One model call before anything is deleted, in which the model
         may persist ONE fact to tier 1. After step 2 the observations it came
         from are references only, so this is the last moment it is in context.
      2. COMPACT, unchanged (9.1): deletion with tombstones. No summarizer. The
         tail of this loop holds only observations - the model's own turns are
         never ledger entries - so there is no conversation history for a
         summarizer to touch without touching tool output. That is the Phase 9
         design STOP.
      3. ROTATE. The session row is closed; a child opens with a FRESH tier-1
         snapshot and a record of what seeded it.
      4. REBUILD the prefix from the child's snapshot under a new PrefixGuard.
         A boundary is the one place the prefix may legitimately change, and
         the only way a fact flushed in step 1 reaches it.
    """
    flushed, record = _flush_memory(ctx, task, builder, prefix, entries, step, plan)
    # The TAIL is compacted to 0.20 of the window LESS THE PREFIX. The ceiling
    # (0.28) counts the prefix and this target did not, so a prefix over 8% of
    # the window left prefix + target above the ceiling: the first boundary
    # deleted nothing, each later one a single entry, the context never got
    # back under the ceiling, and every step paid a flush call. Found by
    # bench/g1_close.py: +55% sent tokens against compaction off (FINDINGS 1.35).
    entries = compact(entries, target_tokens=max(
        0, int(window * 0.20) - count_tokens(prefix))) + flushed
    tombstoned = sum(1 for e in entries if e.kind == "tombstone")
    # The seed is a RECORD of what the child starts from: referenced by the
    # session row, committed to by `session.opened` on the chain. It gets no
    # `artifact.written` event, deliberately: that event feeds the taint
    # rebuild, and a seed holds the objective and tombstone text beside the
    # observations, so observing it as untrusted in a restarted process would
    # make words from the task's own goal untrusted there and not here - the
    # lifetime asymmetry this phase spent its first half removing.
    seed = write_artifact(
        ctx.conn, task.id, "session_seed", render_tail(entries),
        join_taint(*(e.taint for e in entries)), {"step": step})
    rot = ctx.rotate_session(task.id, seed, {**record, "step": step,
                                             "tombstoned": tombstoned})
    # The same contract and the same guard rule as the top of the loop. Built
    # from SYSTEM_CONTRACT directly, this was the route by which a
    # code-as-action arm got the `<code>` clause back after its first
    # compaction, and a prefix-stability arm got a guard it is meant not to
    # have - one mechanism, two places, which is the shape DR3 names.
    guard = PrefixGuard() if ctx.config.mechanisms.prefix_stability else None
    builder = PrefixBuilder(
        system_contract(ctx.config.mechanisms.code_as_action),
        skills, json.loads(task.contract.model_dump_json()),
        tool_manual=ctx.tools.manual(), memory=rot["memory"])
    if guard is not None:
        guard.check(builder, task.id)
    return entries, rot["memory"], guard, builder, builder.build()


def _write_park_seed(ctx, task, entries: list[LedgerEntry]) -> None:
    """D28 - the tail as it stood when the task parked, one record per entry.

    STRUCTURED, each entry with its own taint - not the rendered tail. The
    session boundary's seed is one rendered blob carrying the join of
    everything in it, in practice UNTRUSTED. Restored that way, the objective's
    own words would be untrusted to the argument-taint check, and every action
    quoting them would park: on a channel task, a human interrupted for no
    security gain.

    No `artifact.written`, for the boundary seed's reason: that event feeds the
    taint rebuild, and a seed holds trusted notes beside observations. The
    chained `task.park_seed` event commits to the payload's hash instead.
    """
    payload = json.dumps(
        [{"kind": e.kind, "content": e.content, "artifact_id": e.artifact_id,
          "taint": e.taint.value, "pinned": e.pinned, "label": e.label,
          "exact_strings": list(e.exact_strings)} for e in entries],
        sort_keys=True)
    aid = write_artifact(ctx.conn, task.id, "park_seed", payload,
                         join_taint(*(e.taint for e in entries)),
                         {"entries": len(entries)})
    ctx.log.append(task.id, "task.park_seed",
                   {"artifact": aid, "sha256": sha256_hex(payload),
                    "entries": len(entries)})


# How many earlier observations a resumed task is told about by id. The rest
# are counted; every id stays reachable through the record either way.
_MAX_LISTED = 100


def _restore_park_seed(ctx, task, fresh: list[LedgerEntry]) -> list[LedgerEntry]:
    """D28 - a resumed task sees what it saw when it parked.

    It used to receive its objective, its plan and the released decisions, and
    nothing it had observed - and no tool could reach the rest: `search_history`
    does not index tool output, and `expand_artifact` needs ids that only the
    lost tail carried. So: the last park's seed, verified against the hash its
    chained event committed to, each entry restored with its own taint; then
    the id of every earlier observation.

    The step counter is NOT restored. Receipt keys are built from it and
    execute-once works as designed; renumbering would be its own decision.
    """
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
                continue            # the objective and the plan, re-seeded current
            e = LedgerEntry(kind=d["kind"], content=d["content"],
                            artifact_id=d.get("artifact_id"), taint=Taint(d["taint"]),
                            pinned=bool(d.get("pinned")), label=d.get("label", ""),
                            exact_strings=list(d.get("exact_strings") or []))
            # The gate knows what the model is shown, entry by entry.
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
    """Act on the human decisions made while this task was parked.

    FOUND 2026-09-13, and the worst defect of the phase: before this, NOTHING
    READ AN APPROVAL ONCE IT WAS DECIDED. A task parked; a human pressed
    approve; the decision was finalised, re-verified, anchored and logged; the
    task resumed from its goal; the model re-issued the call; the gate parked
    it again under a NEW approval. The approved action never happened.
    Measured, not inferred: run 1 parks, approve, run 2 issues the identical
    call and parks again, and the file is never written.

    Every test of the approval path checked that the decision was RECORDED -
    finality, sender re-verification, who approved - and none checked that it
    had an EFFECT. The adjacency family in its plainest form: a status value
    written by three code paths and read by none.

    The rule derived from the fact:

      approved  execute the recorded call through `_perform`, so it gets the
                same write-before-act, receipt and observation as any other.
                The receipt key uses the step it parked at, so a crash between
                executing and marking it consumed cannot perform it twice.
      denied    hand the refusal back as an observation, so the resumed run
                knows a human said no rather than rediscovering it by asking.

    What a human CANNOT approve past is re-checked first: exposure, and the
    gate's hard denials (taint ceiling, protected paths, scope). A task whose
    policy narrowed while it was parked does not get the old authority back
    because someone approved under the old policy. `needs_approval` is the
    only verdict a human's decision covers.
    """
    rows = ctx.conn.execute(
        """SELECT id, step_id, tool, args_json, status FROM approvals
           WHERE task_id=? AND status IN ('approved', 'denied')
             AND consumed_at IS NULL
           ORDER BY created_at, id""",
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

    # B3. Exposure is checked BEFORE the capability gate, because they answer
    # different questions and the order matters for what gets recorded. The
    # gate asks "are these arguments safe for this tool"; exposure asks "may
    # this agent call this tool at all". A child denied `git_push` should be
    # refused for not being allowed it, not for whatever its arguments
    # happened to be tainted with.
    try:
        exposure_check(ctx.tool_policy(task.id), tool_name)
    except ToolNotExposed as e:
        # Refused at dispatch and LOGGED. An agent that cannot see a tool
        # works around its absence silently; an agent that is refused leaves
        # a record of what it tried, and the record is the point.
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

    return _perform(ctx, task, tool, tool_name, args, step)


def _perform(ctx, task, tool, tool_name: str, args: dict, step: int) -> LedgerEntry:
    """Execute a call that has ALREADY been cleared, and record it.

    Split out of `execute_action` so a human-approved call released on resume
    goes through the same write-before-act, receipt and observation path as a
    call cleared in the same turn. Two copies of this would be two places for
    the receipt protocol to drift apart.
    """
    # Write-before-act, then execute at most once across all replays.
    key = idempotency_key(task.id, step, tool_name, args)
    intent = {"tool": tool_name, "step": step, "key": key,
              "preview": render_preview(tool, args)}
    if tool_name == "run_code":
        intent.update(_code_record(ctx, task, str(args.get("code", ""))))
    ctx.log.append(task.id, "tool.intent", intent)

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

    # A2-4: what the program touched goes on the RECEIPT and never into the
    # observation - the model's view of a run stays byte-identical.
    touched = result.pop("touched", None) if isinstance(result, dict) else None
    receipt = {"tool": tool_name, "key": key, "ok": bool(result.get("ok", True)),
               "wall_ms": int((time.time() - t0) * 1000)}
    if tool_name == "run_code":
        receipt.update(_touched_record(touched))
        # Which limits held this run, and HOW - kernel or watchdog. A macOS
        # memory cap is reactive, and the record says so rather than a reader
        # assuming the Windows guarantee (agent/platform/darwin.py).
        receipt["limits"] = list(result.get("notes") or []) if isinstance(result, dict) else []
    ctx.log.append(task.id, "tool.receipt", receipt)

    text = _render_result(result)
    taint = tool.output_taint
    origin = {"tool": tool_name,
              "args": {k: str(v)[:200] for k, v in args.items()}}
    # A4.3. A tool that knows where its bytes came from says so, and the
    # fetcher is the only layer that can: the final URL after redirects and
    # the hash of what actually arrived. Reconstructing that at the call site
    # would record what was ASKED for, which differs from what was received
    # exactly when it matters. G4's skill provenance needs this — a skill
    # generated from a page is only checkable if the page can be re-fetched.
    if isinstance(result, dict) and isinstance(result.get("provenance"), dict):
        origin["provenance"] = result["provenance"]
    aid = write_artifact(
        ctx.conn, task.id, "tool_output", text, taint, origin,
    )
    # The TAINT is recorded per write, on the chain. The artifact row is
    # content-addressed and keeps only the FIRST writer's taint (D23's shape),
    # so rebuilding a task's taint state from rows would answer for the wrong
    # writer. See AgentContext.taint_tracker.
    ctx.log.append(task.id, "artifact.written",
                   {"id": aid, "kind": "tool_output", "taint": taint.value})
    ctx.taint_tracker(task.id).observe(text, taint)

    rendered, via = condense_observation(ctx, task, text, taint, aid)
    label = f"{tool_name} via {via}" if via else tool_name
    return LedgerEntry.observation(rendered, artifact_id=aid, taint=taint, label=label)


def _code_record(ctx, task, code: str) -> dict:
    """A2-4 - what run_code RAN, recoverable from the record byte for byte.

    The intent's preview keeps an 80-character repr of `code` and the output
    artifact's origin 200 characters, so the record could not say what a
    program did - the question Part 1 had to leave half-answered. The source
    is stored content-addressed, and its hash rides the chained intent: a row
    edited later no longer matches the chain. No `artifact.written`: source is
    not an observation, and taint is rebuilt from observations only.
    """
    from ..kernel.events import sha256_hex

    taint = ctx.taint_tracker(task.id).args_taint({"code": code})
    aid = write_artifact(ctx.conn, task.id, "code", code, taint,
                         {"tool": "run_code", "role": "source"})
    return {"code_sha256": sha256_hex(code), "code_artifact": aid,
            "code_chars": len(code)}


def _touched_record(touched: dict | None) -> dict:
    """The receipt's view of what a program touched. None when nothing
    captured it - a malformed action, or a run with no touch log - because
    unmeasured is null, never an empty list (invariant 36)."""
    if touched is None:
        return {"code_touched": None}
    return {"code_touched": touched["entries"],
            "code_touched_dropped": touched["dropped"],
            "code_denied": touched["denied"]}


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
        resp = ctx.call_model(
            task, backend, "compress_tool_output",
            prefix=prefix,
            tail=f"<question>{question}</question>\n<data>\n{text[:60000]}\n</data>",
            tools=None,  # tools=None is the quarantine property. Never pass tools here.
            max_tokens=512,
            temperature=0.0,
        )
        return {"text": resp.text}

    try:
        if ctx.config.mechanisms.semantic_cache:
            value, hit = cached_op(ctx.conn, op, text, taint, _summarize)
        else:
            # The semantic-cache arm. Neither read NOR written: a bypass that
            # still filled the store would leave a warm cache for whatever ran
            # next, and on a paired trial that is the ablated arm paying for
            # the other one's hits. Every compression here costs a model call,
            # which on a CPU tier is the 30+ seconds the cache exists to save.
            value, hit = _summarize(), False
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


def _adjudicate(ctx, task, tool, args, decision: GateDecision, step: int, plan,
                park: bool = True) -> GateDecision:
    """Either the deputy decides, or the task parks for a human."""
    from ..plan.review import ReviewRequest, auto_review, tier_of

    tracker = ctx.taint_tracker(task.id)
    arg_taint = tracker.args_taint(args)
    # The TRUSTED tier is human-only IN CODE, not by config (D29's sweep). An
    # empty `human_only_tiers` routed a trusted-tier call to the deputy, and
    # "trusted always parks" then rested on git_push's taint ceiling alone.
    tier = tier_of(ctx, tool.name)
    human_only = tier == "trusted" or tier in ctx.config.autonomy.human_only_tiers

    # A4.2 — a channel task never reaches the deputy. The deputy is a model,
    # and the premise of a channel is that its input was written by someone
    # who is not at this keyboard; a model adjudicating a decision that
    # attacker text has already influenced is the one place a human is worth
    # the wait. This is a wider rule than "TRUSTED always parks", on purpose:
    # the tool's tier says how dangerous the ACTION is, and origin says how
    # much the REQUEST can be trusted, and those are different questions.
    # 11.2 widened this from channel tasks to every UNATTENDED origin. A
    # scheduled task hitting a boundary at 3am parks and notifies; approving it
    # automatically "to make cron useful" removes the permission layer for
    # exactly the tasks running with nobody present.
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
        # escalate → fall through and park

    if not park:
        # A call that cannot wait for a human - the pre-compaction flush - is
        # refused rather than parked. See `_flush_memory`.
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
        # Correct work, no <final>. A pass, counted separately.
        "done_unannounced": TaskStatus.DONE,
        "budget_exhausted": TaskStatus.FAILED,
        "max_steps": TaskStatus.FAILED,
        "plateau": TaskStatus.FAILED,
        # A material departure from the approved plan is a human's call, not
        # a failure (M13.4). It parks rather than dying.
        "needs_replan": TaskStatus.PAUSED,
        "failed": TaskStatus.FAILED,
        "crashed": TaskStatus.FAILED,
        # Recorded so a stalled task is never silently a pass; the caller
        # re-runs it and excludes it if it stalls again.
        "model_stalled": TaskStatus.FAILED,
    }
    set_status(ctx.conn, ctx.log, task, mapping.get(status, TaskStatus.FAILED), status)
    # The session ends with the task. A PAUSED task keeps its session open, so
    # a resume continues it under the same tier-1 snapshot (kernel/sessions.py).
    if mapping.get(status, TaskStatus.FAILED) != TaskStatus.PAUSED:
        ctx.end_session(task.id, status)
    checkpoint(ctx.log, ctx.anchor, task.id)

    # M9.5 needs this or decay has no data: without invocation records every
    # promoted skill looks unused and the next decay pass demotes all of them.
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
