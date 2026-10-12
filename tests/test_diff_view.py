from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.execute import shadow
from agent.execute.diffview import task_diffs
from agent.execute.loop import run_task

pytestmark = pytest.mark.skipif(not shadow.git_available(), reason="git is not installed")

FINAL = '<final>{"answer": "ok"}</final>'
CONTROL = Path(__file__).resolve().parents[1] / "src" / "agent" / "control"


def _write(content, path="work/a.txt"):
    return '<action tool="write_file">' + json.dumps({"path": path, "content": content}) + "</action>"


@pytest.fixture
def run(ctx, task, workspace):
    ctx.config.mechanisms.shadow_checkpoints = True
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.backends["cloud"].queue(_write("one\n"), _write("one\ntwo\n"), _write("side\n", "work/b.txt"), FINAL)
    run_task(ctx, task.id)
    return ctx, task


def test_each_step_shows_what_it_changed(run):
    ctx, task = run
    d = task_diffs(ctx, task.id)
    assert [x["step"] for x in d] == [0, 1, 2]
    assert "+one" in d[0]["diff"] and d[0]["files"] == ["work/a.txt"]
    assert "+two" in d[1]["diff"] and "+one" not in d[1]["diff"].replace("one\n+two", "")
    assert d[2]["files"] == ["work/b.txt"] and "+side" in d[2]["diff"]


def test_each_step_names_the_calls_that_made_it(run):
    ctx, task = run
    d = task_diffs(ctx, task.id)
    assert "write_file" in d[0]["tools"][0] and "a.txt" in d[0]["tools"][0]


def test_one_step_can_be_asked_for(run):
    ctx, task = run
    d = task_diffs(ctx, task.id, step=1)
    assert [x["step"] for x in d] == [1]


def test_a_task_that_changed_nothing_has_no_diffs(ctx, task):
    ctx.config.mechanisms.shadow_checkpoints = True
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, task.id)
    assert task_diffs(ctx, task.id) == []


def test_a_huge_diff_is_cut_and_says_so(ctx, task, workspace):
    ctx.config.mechanisms.shadow_checkpoints = True
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.backends["cloud"].queue(_write("x\n" * 60000), FINAL)
    run_task(ctx, task.id)
    d = task_diffs(ctx, task.id)[0]
    assert len(d["diff"]) < 52000 and d["truncated"] is True


def test_the_cli_prints_them(run, capsys):
    from rich.console import Console

    from agent.cli import build_parser

    ctx, task = run
    args = build_parser().parse_args(["diff", task.id])
    assert args.fn(ctx, args, Console(width=200)) == 0
    out = capsys.readouterr().out
    assert "step 0" in out and "+two" in out and "work/b.txt" in out


def test_the_console_route_returns_them(run):
    from fastapi.testclient import TestClient

    from agent.control.server import create_app

    ctx, task = run
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    got = c.get(f"/tasks/{task.id}/diff").json()
    assert [x["step"] for x in got] == [0, 1, 2]
    assert [x["step"] for x in c.get(f"/tasks/{task.id}/diff?step=2").json()] == [2]


def test_the_console_shows_them_escaped_and_keeps_the_poll_off_while_open():
    js = (CONTROL / "ui.js").read_text(encoding="utf-8")
    assert "showChanges" in js and "/diff" in js
    poll = next(line for line in js.splitlines() if "setInterval" in line and "refreshThread" in line)
    assert "!S.changes" in poll
    body = js[js.index("function changesBody"):js.index("async function showChanges")]
    assert "esc(" in body
