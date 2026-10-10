from __future__ import annotations

import json

import pytest

from agent.execute.loop import run_task

FINAL = '<final>{"answer": "ok"}</final>'


def _act(tool, **args):
    return f'<action tool="{tool}">{json.dumps(args)}</action>'


READ = _act("read_file", path="work/a.txt")
OTHER = _act("list_dir", path="work")


@pytest.fixture
def go(ctx, task, workspace):
    (workspace / "a.txt").write_text("hello\n", encoding="utf-8")
    ctx.config.limits.max_steps = 14
    ctx.config.autonomy.approve_scoped_writes = True

    def run(*script):
        ctx.backends["cloud"].queue(*script)
        res = run_task(ctx, task.id)
        ev = [r[0] for r in ctx.conn.execute(
            "SELECT type FROM events WHERE task_id=? AND type LIKE 'stuck.%' ORDER BY seq", (task.id,))]
        return res, ev, ctx.backends["cloud"].calls
    return run


def test_the_third_identical_call_is_told_to_change_approach(go):
    res, ev, calls = go(READ, READ, READ, OTHER, FINAL)
    assert res.status.startswith("done") and ev == ["stuck.warned"]
    assert "change approach" in calls[3]["tail"].lower()


def test_the_sixth_halts_the_task(go):
    res, ev, _ = go(*[READ] * 8)
    assert res.status == "stuck" and ev == ["stuck.warned", "stuck.detected"]
    assert res.steps <= 6


def test_other_calls_in_between_do_not_reset_the_count(go):
    res, ev, _ = go(READ, OTHER, READ, OTHER, READ, FINAL)
    assert ev == ["stuck.warned"]


def test_a_successful_write_resets_it(go):
    write = _act("write_file", path="work/b.txt", content="x")
    res, ev, _ = go(READ, READ, write, READ, READ, FINAL)
    assert ev == [] and res.status.startswith("done")


def test_different_arguments_are_different_calls(go):
    res, ev, _ = go(_act("read_file", path="work/a.txt"), _act("read_file", path="work/./a.txt"),
                    _act("read_file", path="work/a.txt "), FINAL)
    assert ev == []


def test_a_model_that_repeats_the_same_unparseable_reply_is_halted(go):
    res, ev, _ = go(*["no block at all"] * 8)
    assert res.status == "stuck", (res, ev)


def test_the_mechanism_can_be_ablated(ctx, go):
    ctx.config.mechanisms.stuck_detection = False
    res, ev, _ = go(*[READ] * 8)
    assert ev == [] and res.status != "stuck" and "stuck_detection" in ctx.config.mechanisms.ablated()
