"""Invariant 17 — the approved plan hash is verified before every step.

THIS INVARIANT HAS NEVER FIRED IN PRODUCTION.

`verify_plan_pin` compared the recomputed plan hash against
`ctx.anchor.latest(task.id)` and only acted `if anchored.startswith("plan:")`.
But `checkpoint()` wrote chain heads to that same key, and `run_task`
checkpoints on every run — so after the first step the latest anchor entry
was a chain head, the `startswith` was False, and the anchor comparison was
skipped. Silently. Every time. (D21.)

The DB half of the check — recomputed hash against the stored `approved_hash`
— did keep working, so a plan altered in the `plans` table was still caught.
What was lost is the ANCHOR half: the copy held outside the database, which
is the entire reason there is an anchor. A writer with database access could
edit both the plan and its stored hash and the check would have agreed with
itself.

So this file injects rather than inspects. A green suite proved nothing about
this check for as long as it has existed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.config import BackendConfig, Config  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import run_task  # noqa: E402
from agent.kernel.integrity import IntegrityError, checkpoint  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.plan.planner import approve_plan, verify_plan_pin  # noqa: E402
from agent.plan.types import Plan, PlanStep  # noqa: E402


def make_ctx(tmp_path):
    (tmp_path / "work").mkdir(exist_ok=True)
    cfg = Config(
        profile="mixed",
        db_path=str(tmp_path / "t.db"),
        anchor_path=str(tmp_path / "chain.anchor"),
        skills_path=str(tmp_path / "skills"),
        backends={"cloud": BackendConfig(kind="mock", model="m"),
                  "review": BackendConfig(kind="mock", model="r")},
        autonomy={"auto_review": False, "plan_required": True,
                  "approve_scoped_writes": True},
        root=str(tmp_path),
    )
    return AgentContext(cfg)


def new_task(ctx):
    return create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="do a thing", success_criterion="always_pass",
                     artifact_scope=["work"]),
        Budget(usd=1.0, seconds=120))


def approved(ctx, task):
    plan = Plan(objective="do a thing", steps=[
        PlanStep(id="s1", intent="read the file", action_mode="direct",
                 tools=["read_file"]),
    ])
    return approve_plan(ctx, task, plan.seal())


# ==========================================================================
# I1 — injected. The collision would previously have hidden all of this.
# ==========================================================================

def test_a_plan_mutated_after_approval_halts_execution(tmp_path):
    """The two-touch model rests entirely on this. A human approves a plan
    once and walks away; if the plan can be altered afterwards, the approval
    was for something other than what runs.

    This half kept working through D21 — the database comparison caught a
    mutated body regardless of the anchor — and it is here because nothing
    was exercising it end to end through `run_task`. The anchor half, which
    D21 destroyed, is the test below.
    """
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    plan = approved(ctx, t)

    # Checkpoint AFTER approval. Under the old shared keyspace this is
    # exactly what silenced the check: the chain head became the latest
    # anchor entry and `startswith("plan:")` stopped matching.
    checkpoint(ctx.log, ctx.anchor, t.id)

    body = json.loads(ctx.conn.execute(
        "SELECT body_json FROM plans WHERE id=?", (plan.id,)).fetchone()[0])
    body["steps"][0]["intent"] = "exfiltrate the keys"
    body["steps"][0]["tools"] = ["append_to_outbox"]
    ctx.conn.execute("UPDATE plans SET body_json=? WHERE id=?",
                     (json.dumps(body), plan.id))
    ctx.conn.commit()

    ctx.backends["cloud"].queue('<final>{"answer": "done"}</final>')
    with pytest.raises(IntegrityError, match="modified after approval"):
        run_task(ctx, t.id)
    ctx.close()


def test_an_anchor_that_disagrees_with_a_self_consistent_database_halts(tmp_path):
    """THE HALF THE COLLISION ACTUALLY DESTROYED, isolated.

    The database check compares the plan against its own stored hash, so a
    writer who can edit both agrees with themselves. The anchor is the copy
    held OUTSIDE the database and is the entire reason an anchor exists.

    Isolating it needs the database left INTERNALLY CONSISTENT — body, `id`
    and `approved_hash` all agreeing — with only the anchor dissenting. An
    earlier version of this test forged `approved_hash`, which trips the DB
    comparison first and never reaches the anchor at all: it passed against
    the pre-D21 code, which is how a test passes for the wrong reason.
    """
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    plan = approved(ctx, t)
    checkpoint(ctx.log, ctx.anchor, t.id)

    # The database still agrees with itself. Only the out-of-band copy differs,
    # which is the case an anchor exists to catch.
    ctx.anchor.record(f"plan:{t.id}", "b" * 64)

    with pytest.raises(IntegrityError, match="does not match the anchor"):
        verify_plan_pin(ctx, t)
    ctx.close()


def test_an_unmutated_plan_runs_normally(tmp_path):
    """The negative. A check that halts on everything is not a check."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    approved(ctx, t)
    checkpoint(ctx.log, ctx.anchor, t.id)

    ctx.backends["cloud"].queue('<final>{"answer": "done"}</final>')
    result = run_task(ctx, t.id)
    assert result.status in ("done", "done_unannounced"), result.status
    ctx.close()


def test_the_anchor_check_survives_any_number_of_later_checkpoints(tmp_path):
    """The regression, stated as the property that was broken. `run_task`
    checkpoints on every run, so 'a checkpoint came after the pin' is the
    normal case, not an edge one."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    plan = approved(ctx, t)
    for i in range(5):
        ctx.log.append(t.id, "task.budget_spent", {"i": i})
        checkpoint(ctx.log, ctx.anchor, t.id)

    assert ctx.anchor.latest(f"plan:{t.id}") == plan.id, (
        "the pin must still be findable after five checkpoints")
    assert verify_plan_pin(ctx, t).id == plan.id
    ctx.close()


def test_the_pin_is_checked_before_every_step_not_only_the_first(tmp_path):
    """Invariant 17 says EVERY step. A plan swapped at step three is the
    interesting attack, because the first step's check already passed."""
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(
        encoding="utf-8")
    i = src.index("for step in range(ctx.config.limits.max_steps)")
    # The step's model call, through the recording door since D36.
    j = src.index("resp = ctx.call_model(", i)
    assert "verify_plan_pin(ctx, task)" in src[i:j], (
        "the pin check must be inside the step loop, before the model call")

    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    plan = approved(ctx, t)
    checkpoint(ctx.log, ctx.anchor, t.id)

    calls = {"n": 0}
    real = verify_plan_pin

    import agent.plan.planner as planner_mod

    def counting(c, task):
        calls["n"] += 1
        return real(c, task)

    planner_mod.verify_plan_pin = counting
    try:
        ctx.backends["cloud"].queue(
            '<action tool="list_dir">{"path": "work"}</action>',
            '<action tool="list_dir">{"path": "work"}</action>',
            '<final>{"answer": "done"}</final>')
        run_task(ctx, t.id)
    finally:
        planner_mod.verify_plan_pin = real
    assert calls["n"] >= 3, (
        f"the pin was checked {calls['n']} times across 3 steps; invariant 17 "
        "says every step")
    ctx.close()
