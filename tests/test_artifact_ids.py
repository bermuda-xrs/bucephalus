from __future__ import annotations

from agent.execute.loop import run_task
from agent.kernel.tasks import create_task
from agent.kernel.types import Taint
from agent.ledger.artifacts import read_artifact, write_artifact

FINAL = '<final>{"answer": "ok"}</final>'


def _answer(ctx, task_id):
    row = ctx.conn.execute("SELECT content FROM artifacts WHERE task_id=? AND kind='answer'", (task_id,)).fetchone()
    return row[0] if row else None


def test_two_tasks_with_the_same_answer_each_keep_it(ctx, task):
    ids = []
    for _ in range(2):
        t = create_task(ctx.conn, ctx.log, task.contract, task.budget)
        ctx.backends["cloud"].queue(FINAL)
        run_task(ctx, t.id)
        ids.append(t.id)
    assert _answer(ctx, ids[0]) is not None and _answer(ctx, ids[1]) is not None


def test_the_same_text_under_another_taint_or_kind_is_its_own_artifact(ctx, task):
    a = write_artifact(ctx.conn, task.id, "tool_output", "same", Taint.TRUSTED, {})
    b = write_artifact(ctx.conn, task.id, "tool_output", "same", Taint.UNTRUSTED, {})
    c = write_artifact(ctx.conn, task.id, "answer", "same", Taint.TRUSTED, {})
    assert len({a, b, c}) == 3 and read_artifact(ctx.conn, b)["taint"] == Taint.UNTRUSTED
    assert write_artifact(ctx.conn, task.id, "tool_output", "same", Taint.TRUSTED, {}) == a
