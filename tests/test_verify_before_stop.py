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


@pytest.fixture
def full(ctx, task):
    ctx.config.autonomy.approve_scoped_writes = True

    def go(*script):
        t = create_task(ctx.conn, ctx.log, task.contract, task.budget)
        ctx.backends["cloud"].queue(*script)
        res = run_task(ctx, t.id)
        n = ctx.conn.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND type='verify.continued'",
                             (t.id,)).fetchone()[0]
        return res, n, ctx.backends["cloud"].calls
    return go


CODE = _act("run_code", code="import runpy; runpy.run_path('work/prog.py')")


def test_the_full_kit_is_held_with_a_note_naming_run_code(full, ctx):
    assert "run_code" in ctx.tools.names() and "run_command" not in ctx.tools.names()
    res, n, calls = full(WRITE, FINAL, CODE, FINAL)
    assert res.status.startswith("done") and n == 1
    note = calls[2]["tail"][-600:]
    assert "have not run a test, lint or the program" in note and "run_code" in note and "run_command" not in note


def test_a_run_code_after_the_change_releases_the_full_kit(full):
    res, n, _ = full(WRITE, CODE, FINAL)
    assert res.status.startswith("done") and n == 0


def test_a_failing_run_code_does_not_release_it(full):
    res, n, _ = full(WRITE, _act("run_code", code="raise SystemExit(1)"), FINAL, CODE, FINAL)
    assert n == 1


def test_a_run_code_before_the_change_does_not_count(full):
    res, n, _ = full(_act("run_code", code="pass"), WRITE, FINAL, CODE, FINAL)
    assert n == 1


def test_the_schema_note_names_the_kits_runner(ctx, small):
    from agent.execute.loop import _verify_note

    assert "run_code" in _verify_note(ctx) and "run_command" not in _verify_note(ctx)
    assert "run_command" in _verify_note(small)


def test_with_nothing_to_run_the_rule_does_not_apply(ctx, task, scripted):
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.tools = ctx.tools.without("run_code")
    scripted("cloud", _act("write_file", path="work/a.txt", content="x"), FINAL)
    res = run_task(ctx, task.id)
    assert res.status.startswith("done")
    assert not ctx.conn.execute("SELECT 1 FROM events WHERE task_id=? AND type='verify.continued'", (task.id,)).fetchone()
