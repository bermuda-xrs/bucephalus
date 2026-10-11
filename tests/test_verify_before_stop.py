from __future__ import annotations

import json

import pytest

from agent.execute.loop import run_task
from agent.kernel.tasks import create_task

FINAL = '<final>{"answer": "done"}</final>'


def _act(tool, **args):
    return f'<action tool="{tool}">{json.dumps(args)}</action>'


@pytest.fixture
def run(small, task):
    def go(*script):
        t = create_task(small.conn, small.log, task.contract, task.budget)
        small.backends["cloud"].queue(*script)
        res = run_task(small, t.id)
        n = small.conn.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND type='verify.continued'",
                               (t.id,)).fetchone()[0]
        return res, n, small.backends["cloud"].calls
    return go


WRITE = _act("write_file", path="work/prog.py", content="print('hi')\n")
RUN = _act("run_command", command="python work/prog.py")


def test_a_final_after_a_write_with_no_check_is_turned_into_a_continuation(run):
    res, n, calls = run(WRITE, FINAL, RUN, FINAL)
    assert res.status.startswith("done") and n == 1
    assert "have not run a test, lint or the program" in calls[2]["tail"]


def test_it_is_capped_at_three(run):
    res, n, _ = run(WRITE, FINAL, FINAL, FINAL, FINAL)
    assert n == 3 and res.status.startswith("done"), (res, n)


def test_no_write_means_no_bounce(run, small):
    small.backends["cloud"].cfg.read_first = False
    res, n, _ = run(FINAL)
    assert res.status.startswith("done") and n == 0


def test_a_check_after_the_write_lets_the_final_through(run):
    res, n, _ = run(WRITE, RUN, FINAL)
    assert res.status.startswith("done") and n == 0


def test_a_check_before_the_write_does_not_count(run):
    res, n, _ = run(RUN, WRITE, FINAL, RUN, FINAL)
    assert n == 1


def test_a_refused_command_is_not_a_check(run):
    res, n, _ = run(WRITE, _act("run_command", command="rm -rf work"), FINAL, RUN, FINAL)
    assert n == 1


def test_a_failing_test_still_counts_as_having_run_one(run, workspace):
    (workspace / "test_x.py").write_text("def test_a():\n    assert False\n", encoding="utf-8")
    res, n, _ = run(WRITE, _act("run_command", command="pytest work/test_x.py -q"), FINAL)
    assert n == 0 and res.status.startswith("done")


def test_the_mechanism_can_be_ablated(small, run):
    small.config.mechanisms.verify_before_stop = False
    res, n, _ = run(WRITE, FINAL)
    assert n == 0 and "verify_before_stop" in small.config.mechanisms.ablated()


def test_without_run_command_the_rule_does_not_apply(ctx, task, scripted):
    ctx.config.autonomy.approve_scoped_writes = True
    scripted("cloud", _act("write_file", path="work/a.txt", content="x"), FINAL)
    res = run_task(ctx, task.id)
    assert res.status.startswith("done")
    assert not ctx.conn.execute("SELECT 1 FROM events WHERE task_id=? AND type='verify.continued'", (task.id,)).fetchone()
