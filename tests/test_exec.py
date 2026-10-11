from __future__ import annotations

import json

import pytest
from rich.console import Console

from agent.cli import build_parser

FINAL = '<final>{"answer": "all done"}</final>'
LIST = '<action tool="list_dir">{"path": "work"}</action>'
PLAN = json.dumps({"objective": "g", "steps": [{"id": "1", "intent": "look", "tools": ["list_dir"],
                                                "artifact_scope": ["work"]}]})


@pytest.fixture
def exec_(ctx, workspace, capsys):
    def go(*argv, script=()):
        ctx.backends["cloud"].queue(*script)
        args = build_parser().parse_args(["exec", "do the thing", "--scope", "work", *argv])
        code = args.fn(ctx, args, Console(file=open(__import__("os").devnull, "w")))
        out = capsys.readouterr().out
        return code, out

    return go


def _lines(out):
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_stream_json_is_one_object_per_line_and_ends_with_the_result(exec_):
    code, out = exec_("--output-format", "stream-json", script=(LIST, FINAL))
    rows = _lines(out)
    assert code == 0
    assert rows[0]["type"] == "task.created" and rows[0]["task_id"].startswith("t_")
    kinds = [r["type"] for r in rows]
    assert "tool.called" in kinds and "tool.returned" in kinds and "live.step" in kinds
    last = rows[-1]
    assert last["type"] == "result" and last["status"].startswith("done") and last["answer"] == "all done"
    assert last["exit_code"] == 0 and last["task_id"] == rows[0]["task_id"]


def test_text_output_is_just_the_answer(exec_):
    code, out = exec_(script=(FINAL,))
    assert code == 0 and out.strip() == "all done"


def test_a_task_that_does_not_finish_exits_2(ctx, exec_):
    ctx.config.limits.max_steps = 2
    code, out = exec_("--output-format", "stream-json", script=(LIST,))
    assert code == 2 and _lines(out)[-1]["exit_code"] == 2


def test_without_the_flag_a_plan_is_drafted_and_nothing_runs(ctx, exec_):
    ctx.config.autonomy.plan_required = True
    code, out = exec_("--output-format", "stream-json", script=(PLAN, FINAL))
    rows = _lines(out)
    assert code == 3 and rows[-1]["status"] == "plan_required" and rows[-1]["plan_id"]
    assert not [c for c in ctx.backends["cloud"].calls if "<task_contract>" in c["prefix"]]


def test_the_flag_is_the_operators_approval_of_that_plan(ctx, exec_):
    ctx.config.autonomy.plan_required = True
    code, out = exec_("--output-format", "stream-json", "--approve-plan", script=(PLAN, FINAL))
    kinds = [r["type"] for r in _lines(out)]
    assert code == 0 and "plan.drafted" in kinds and "plan.approved" in kinds


def test_an_approval_the_task_needs_exits_4_with_its_id(ctx, exec_):
    ctx.config.autonomy.approve_scoped_writes = False
    ctx.config.autonomy.auto_review = False
    write = '<action tool="write_file">' + json.dumps({"path": "work/a.txt", "content": "x"}) + "</action>"
    code, out = exec_("--output-format", "stream-json", script=(write,))
    last = _lines(out)[-1]
    assert code == 4 and last["status"] == "paused" and last["approval_id"]


def test_stdout_carries_nothing_but_the_stream(exec_):
    _, out = exec_("--output-format", "stream-json", script=(FINAL,))
    assert all(line.startswith("{") for line in out.splitlines() if line.strip())
