from __future__ import annotations

import json
from pathlib import Path

from agent import stream
from agent.execute.sandbox import run_sandboxed

CONTROL = Path(__file__).resolve().parents[1] / "src" / "agent" / "control"


def test_the_sandbox_hands_each_line_to_the_caller_as_it_comes():
    got = []
    r = run_sandboxed("import sys\nfor i in range(3):\n    print('line', i, flush=True)\nprint('oops', file=sys.stderr)",
                      on_output=got.append, timeout_s=30)
    assert "line 2" in r["stdout"] and "oops" in r["stderr"] and r["returncode"] == 0
    text = "".join(got)
    assert "line 0" in text and "line 2" in text and "oops" in text


def test_a_run_command_streams_to_the_task_bus(small, task, workspace):
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task

    (workspace / "p.py").write_text("print('hello from the program')\n", encoding="utf-8")
    t = create_task(small.conn, small.log, task.contract, task.budget)
    seen = []
    off = stream.subscribe(t.id, lambda kind, data: seen.append((kind, data)))
    try:
        small.backends["cloud"].queue('<action tool="run_command">' + json.dumps({"command": "python work/p.py"})
                                      + "</action>", '<final>{"answer": "ok"}</final>')
        run_task(small, t.id)
    finally:
        off()
    out = [d for k, d in seen if k == "tool_output"]
    assert out and "hello from the program" in "".join(d["text"] for d in out) and out[0]["tool"] == "run_command"


def test_the_tui_shows_command_output():
    from agent.tui.model import RunState

    v = RunState()
    v.apply_live("tool_output", {"text": "building...\n", "tool": "run_command"})
    assert "building..." in v.text()


def test_the_console_shows_command_output():
    assert '"live.tool_output"' in (CONTROL / "ui.js").read_text(encoding="utf-8")
