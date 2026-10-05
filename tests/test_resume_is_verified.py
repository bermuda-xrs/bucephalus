from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.config import BackendConfig, Config  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import run_task  # noqa: E402
from agent.kernel.integrity import (  # noqa: E402
    ANCHOR_BEHIND,
    IntegrityError,
    checkpoint,
    classify_anchor,
)
from agent.kernel.tasks import create_task, load_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract, TaskStatus  # noqa: E402


def make_ctx(tmp_path):
    (tmp_path / "work").mkdir(exist_ok=True)
    cfg = Config(
        profile="mixed",
        db_path=str(tmp_path / "t.db"),
        anchor_path=str(tmp_path / "chain.anchor"),
        skills_path=str(tmp_path / "skills"),
        backends={"cloud": BackendConfig(kind="mock", model="m"),
                  "review": BackendConfig(kind="mock", model="r")},
        autonomy={"auto_review": False, "plan_required": False,
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


def run_once(ctx, task):
    ctx.backends["cloud"].queue('<final>{"answer": "first"}</final>')
    return run_task(ctx, task.id)


def test_a_broken_chain_halts_the_resume(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    run_once(ctx, t)

    ctx.conn.execute(
        "UPDATE events SET payload_json='{\"tampered\":1}' "
        "WHERE task_id=? AND seq=1", (t.id,))
    ctx.conn.commit()

    ctx.backends["cloud"].queue('<final>{"answer": "second"}</final>')
    with pytest.raises(IntegrityError, match="Chain broken"):
        run_task(ctx, t.id)

    assert load_task(ctx.conn, t.id).status is TaskStatus.FAILED, (
        "a halted task must be recorded as failed, not left running")
    assert ctx.conn.execute(
        "SELECT 1 FROM events WHERE task_id=? AND type='integrity.halt'",
        (t.id,)).fetchall(), "the halt itself must be on the record"
    ctx.close()


def test_an_anchor_merely_behind_resumes_normally(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    run_once(ctx, t)

    checkpoint(ctx.log, ctx.anchor, t.id)
    for i in range(3):
        ctx.log.append(t.id, "task.budget_spent", {"i": i})
    assert classify_anchor(ctx.conn, ctx.log, ctx.anchor, t.id) == ANCHOR_BEHIND

    ctx.backends["cloud"].queue('<final>{"answer": "second"}</final>')
    result = run_task(ctx, t.id)
    assert result.status in ("done", "done_unannounced"), (
        f"a merely-behind anchor must not block a resume: {result.status}")
    ctx.close()


def test_a_rewritten_history_halts_even_though_the_chain_verifies(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    run_once(ctx, t)
    checkpoint(ctx.log, ctx.anchor, t.id)

    anchored = ctx.anchor.latest_chain(t.id)
    seq = ctx.conn.execute(
        "SELECT seq FROM events WHERE task_id=? AND hash=?",
        (t.id, anchored)).fetchone()[0]
    ctx.conn.execute("DELETE FROM events WHERE task_id=? AND seq >= ?",
                     (t.id, seq))
    ctx.conn.commit()
    ctx.log.append(t.id, "task.budget_spent", {"replacement": True})

    assert ctx.log.verify(t.id)[0] is True, "the chain itself still verifies"
    ctx.backends["cloud"].queue('<final>{"answer": "second"}</final>')
    with pytest.raises(IntegrityError, match="rewritten"):
        run_task(ctx, t.id)
    ctx.close()


def test_a_fresh_task_passes_the_door_trivially(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    assert run_once(ctx, t).status in ("done", "done_unannounced")
    ctx.close()


def test_verification_happens_before_the_task_is_marked_running(tmp_path):
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(
        encoding="utf-8")
    i = src.index("def run_task")
    body = src[i:i + 3000]
    assert body.index("resume(ctx.conn") < body.index("set_status(ctx.conn"), (
        "resume() must run before the task is marked RUNNING")


def test_every_continuation_path_goes_through_run_task():
    import ast

    sites = []
    for f in list((ROOT / "src").rglob("*.py")) + list((ROOT / "bench").glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "run_task"):
                sites.append(f"{f.name}:{node.lineno}")
    assert sites, "run_task should have call sites"

    loop = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(
        encoding="utf-8")
    assert "resume(ctx.conn, task.id, ctx.log, ctx.anchor)" in loop, (
        "the door must be inside run_task, so every caller inherits it")


def test_a_plan_pin_does_not_read_as_a_rewritten_chain(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    run_once(ctx, t)
    checkpoint(ctx.log, ctx.anchor, t.id)

    ctx.anchor.record(t.id, "plan:deadbeef")

    assert ctx.anchor.latest(t.id) == "plan:deadbeef", "raw read sees it"
    assert ctx.anchor.latest_chain(t.id) is not None, (
        "the chain reader must skip it and find the real head")
    assert classify_anchor(ctx.conn, ctx.log, ctx.anchor, t.id) is None, (
        "a plan pin must not read as a rewritten history")

    ctx.backends["cloud"].queue('<final>{"answer": "second"}</final>')
    assert run_task(ctx, t.id).status in ("done", "done_unannounced")
    ctx.close()


def test_the_plan_pin_check_survives_a_later_checkpoint(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    ctx.anchor.record(f"plan:{t.id}", "abc123")
    checkpoint(ctx.log, ctx.anchor, t.id)
    ctx.log.append(t.id, "task.budget_spent", {"i": 0})
    checkpoint(ctx.log, ctx.anchor, t.id)

    assert ctx.anchor.latest(f"plan:{t.id}") == "abc123", (
        "later checkpoints must not overwrite the plan pin")
    ctx.close()


def test_the_two_kinds_of_anchor_record_use_different_keys():
    planner = (ROOT / "src" / "agent" / "plan" / "planner.py").read_text(
        encoding="utf-8")
    assert 'record(f"plan:{task.id}"' in planner, (
        "the plan pin must have its own key, not share the task id with "
        "chain checkpoints")
    integrity = (ROOT / "src" / "agent" / "kernel" / "integrity.py").read_text(
        encoding="utf-8")
    assert "anchor.record(task_id, head)" in integrity
