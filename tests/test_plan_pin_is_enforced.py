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


def test_a_plan_mutated_after_approval_halts_execution(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    plan = approved(ctx, t)

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
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    approved(ctx, t)
    checkpoint(ctx.log, ctx.anchor, t.id)

    ctx.anchor.record(f"plan:{t.id}", "b" * 64)

    with pytest.raises(IntegrityError, match="does not match the anchor"):
        verify_plan_pin(ctx, t)
    ctx.close()


def test_an_unmutated_plan_runs_normally(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    approved(ctx, t)
    checkpoint(ctx.log, ctx.anchor, t.id)

    ctx.backends["cloud"].queue('<final>{"answer": "done"}</final>')
    result = run_task(ctx, t.id)
    assert result.status in ("done", "done_unannounced"), result.status
    ctx.close()


def test_the_anchor_check_survives_any_number_of_later_checkpoints(tmp_path):
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
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(
        encoding="utf-8")
    i = src.index("for step in range(ctx.config.limits.max_steps)")
    j = src.index("resp = ctx.call_model(", i)
    assert "verify_plan_pin(ctx, task)" in src[i:j], (
        "the pin check must be inside the step loop, before the model call")

    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    approved(ctx, t)
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
