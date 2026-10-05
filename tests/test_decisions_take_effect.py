from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.execute.loop import run_task
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.security.exposure import ToolPolicy

WRITE = ('<action tool="write_file">'
         + json.dumps({"path": "work/x.txt", "content": "hello"}) + "</action>")
OUTBOX = ('<action tool="append_to_outbox">'
          + json.dumps({"to": "me@example.test", "body": "hi"}) + "</action>")
FINAL = '<final>{"answer": "done"}</final>'


@pytest.fixture
def hctx(ctx):
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = False
    ctx.config.autonomy.plan_required = False
    return ctx


def _task(ctx):
    return create_task(ctx.conn, ctx.log,
                       TaskContract(goal="write a file", artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=600))


def _park(ctx, call=WRITE):
    t = _task(ctx)
    ctx.backends["cloud"].queue(call)
    r = run_task(ctx, t.id)
    assert r.status == "paused", r
    return t, r.approval_id


def _consumed(ctx, approval_id):
    return ctx.conn.execute(
        "SELECT consumed_at FROM approvals WHERE id=?", (approval_id,)
    ).fetchone()[0]


def _outcomes(ctx, task_id):
    return [json.loads(p)["outcome"] for (p,) in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=?",
        (task_id, "approval.consumed"))]


def test_an_approved_action_happens_when_the_task_resumes(hctx):
    t, approval_id = _park(hctx)
    hctx.resolve_approval(approval_id, True)
    hctx.backends["cloud"].queue(FINAL)
    r2 = run_task(hctx, t.id)

    out = Path(hctx.config.root) / "work" / "x.txt"
    assert out.exists() and out.read_text(encoding="utf-8") == "hello", (
        "the human approved this write; before the fix it was never performed")
    assert r2.status == "done"
    assert _consumed(hctx, approval_id) is not None
    assert _outcomes(hctx, t.id) == ["executed"]
    assert hctx.pending_approvals(t.id) == [], "no second park for the same call"
    assert "write_file" in hctx.backends["cloud"].prompts[-1], (
        "the resumed model is told the approved action happened")
    assert hctx.log.verify(t.id)[0] is True


def test_a_denial_reaches_the_model(hctx):
    t, approval_id = _park(hctx)
    hctx.resolve_approval(approval_id, False)
    hctx.backends["cloud"].queue(FINAL)
    run_task(hctx, t.id)

    assert not (Path(hctx.config.root) / "work" / "x.txt").exists()
    assert "A human DENIED" in hctx.backends["cloud"].prompts[-1], (
        "a resumed run that is not told about the denial rediscovers it by "
        "asking again")
    assert _outcomes(hctx, t.id) == ["denied"]


def test_a_released_action_is_performed_at_most_once(hctx):
    t, approval_id = _park(hctx, OUTBOX)
    hctx.resolve_approval(approval_id, True)
    hctx.backends["cloud"].queue(FINAL)
    run_task(hctx, t.id)

    outbox = Path(hctx.config.root) / "runs" / "outbox.log"
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 1

    hctx.conn.execute("UPDATE approvals SET consumed_at=NULL WHERE id=?",
                      (approval_id,))
    hctx.conn.commit()
    hctx.backends["cloud"].queue(FINAL)
    run_task(hctx, t.id)
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 1, (
        "the side effect happened twice")


def test_policy_narrowed_while_parked_voids_the_approval(hctx):
    t, approval_id = _park(hctx)
    hctx.resolve_approval(approval_id, True)
    hctx.restrict_tools(t.id, ToolPolicy(frozenset({"write_file"}), "narrowed"))
    hctx.backends["cloud"].queue(FINAL)
    run_task(hctx, t.id)

    assert not (Path(hctx.config.root) / "work" / "x.txt").exists()
    assert _outcomes(hctx, t.id) == ["void"]
    assert "no longer permitted" in hctx.backends["cloud"].prompts[-1]


def test_an_undecided_approval_is_left_alone(hctx):
    t, approval_id = _park(hctx)
    hctx.backends["cloud"].queue(FINAL)
    run_task(hctx, t.id)
    assert not (Path(hctx.config.root) / "work" / "x.txt").exists()
    assert _consumed(hctx, approval_id) is None
    assert [a["id"] for a in hctx.pending_approvals(t.id)] == [approval_id]
