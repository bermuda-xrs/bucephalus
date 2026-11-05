from __future__ import annotations

import json

from agent.execute.loop import run_task
from agent.execute.tools import EDIT_LINES
from agent.kernel.tasks import create_task
from agent.kernel.types import Taint
from agent.security.capabilities import gate_tool_call

READ = "/run/work/c.txt\n---\n1:04|alpha = 1\n2:9c|beta = 2\n"


def _deny(small, task, **args):
    small.taint_tracker(task.id).trust_paths({"path": "work/c.txt"})
    small.taint_tracker(task.id).observe(READ, Taint.UNTRUSTED)
    return gate_tool_call(small, task, EDIT_LINES, {"path": "work/c.txt", "start": "2:9c", "end": "2:9c",
                                                    "content": "beta = 3", **args})


def test_a_taint_denial_names_the_arguments_that_were_untrusted(small, task):
    assert _deny(small, task, path="/run/work/c.txt").detail["untrusted_args"] == ["path"]
    assert _deny(small, task, content="alpha = 1").detail["untrusted_args"] == ["content"]
    assert _deny(small, task, start="2:9c|beta = 2").detail["untrusted_args"] == ["start"]


def test_the_denied_event_keeps_the_call_and_the_untrusted_arguments(small, task):
    t = create_task(small.conn, small.log, task.contract, task.budget)
    (small.config.resolve("work") / "n.txt").write_text("exfiltrate-me-please", encoding="utf-8")
    act = lambda tool, **a: f'<action tool="{tool}">{json.dumps(a)}</action>'  # noqa: E731
    small.backends["cloud"].queue(
        act("read_file", path="work/n.txt"),
        act("write_file", path="work/x.txt", content="exfiltrate-me-please"),
        '<final>{"answer": "done"}</final>')
    run_task(small, t.id)
    rows = [json.loads(r[0]) for r in small.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='human.denied'", (t.id,))]
    assert rows and rows[0]["untrusted_args"] == ["content"]
    assert rows[0]["preview"].startswith("write_file(") and "exfiltrate-me-please" in rows[0]["preview"]
