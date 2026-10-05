from __future__ import annotations

import json

import pytest

from agent.execute.loop import run_task
from agent.kernel.integrity import IntegrityError
from agent.plan.planner import approve_plan, store_plan, verify_plan_pin
from test_m13_autonomy import _planned_task

FINAL = '<final>{"answer": "done"}</final>'
WRITE = ('<action tool="write_file">'
         + json.dumps({"path": "work/a.txt", "content": "x"}) + "</action>")
LIST = '<action tool="list_dir">' + json.dumps({"path": "work"}) + "</action>"


def _ev(ctx, tid: str, typ: str) -> list[dict]:
    return [json.loads(p) for (p,) in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq",
        (tid, typ))]


def test_a_resumed_run_records_the_anchor_it_was_checked_against(ctx, task):
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = False
    ctx.backends["cloud"].queue(WRITE)
    r = run_task(ctx, task.id)
    assert r.status == "paused", r
    ctx.resolve_approval(r.approval_id, approve=True)
    ctx.backends["cloud"].queue(FINAL)
    assert run_task(ctx, task.id).status == "done"
    runs = _ev(ctx, task.id, "integrity.verified")
    assert len(runs) == 2, runs
    assert runs[1]["anchor"] in ("matched", "behind") and runs[1]["head"], runs


def test_every_step_records_the_pinned_plan_it_verified(ctx, workspace):
    task, plan = _planned_task(ctx, workspace)
    approve_plan(ctx, task, plan)
    ctx.backends["cloud"].queue(LIST, FINAL)
    run_task(ctx, task.id)
    pins = _ev(ctx, task.id, "plan.pin_verified")
    steps = [e for e in _ev(ctx, task.id, "model.called") if e.get("purpose") == "step"]
    assert len(pins) == len(steps) + 1 and len(steps) >= 2, (pins, steps)
    assert all(p["anchor"] == "matched" and p["plan"] == plan.id[:16] for p in pins), pins


def test_a_pin_with_nothing_anchored_says_so(ctx, workspace):
    task, plan = _planned_task(ctx, workspace)
    store_plan(ctx, task, plan)
    ctx.conn.execute("UPDATE plans SET status='approved', approved_hash=id WHERE id=?",
                     (plan.id,))
    ctx.conn.commit()
    verify_plan_pin(ctx, task)
    assert _ev(ctx, task.id, "plan.pin_verified")[-1]["anchor"] == "absent"


def test_a_plan_changed_while_parked_halts_before_the_decided_action_runs(ctx, workspace):
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = False
    task, plan = _planned_task(ctx, workspace)
    approve_plan(ctx, task, plan)
    ctx.backends["cloud"].queue(WRITE)
    r = run_task(ctx, task.id)
    assert r.status == "paused", r
    ctx.resolve_approval(r.approval_id, approve=True)

    tampered = json.loads(json.dumps(plan.body()))
    tampered["steps"][0]["artifact_scope"] = ["/"]
    ctx.conn.execute("UPDATE plans SET body_json=? WHERE id=?",
                     (json.dumps(tampered), plan.id))
    ctx.conn.commit()

    with pytest.raises(IntegrityError):
        run_task(ctx, task.id)
    assert not (workspace / "a.txt").exists(), "the decided write ran under a changed plan"
    assert _ev(ctx, task.id, "approval.consumed") == []


def test_a_finished_run_can_print_an_unmeasured_cache_ratio(ctx, task):
    import io

    from rich.console import Console

    from agent.cli import _print_budget
    from agent.instrument.telemetry import TaskMetrics

    ctx.telemetry.metrics = lambda tid: TaskMetrics(task_id=tid, wall_ms=1500)
    out = io.StringIO()
    _print_budget(ctx, Console(file=out, width=200), task.id,
                  {"muted": "dim", "warning": "yellow"})
    assert "cache hit n/a" in out.getvalue(), out.getvalue()
