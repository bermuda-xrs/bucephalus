from __future__ import annotations

from pathlib import Path

from agent.execute.tools import EDIT_LINES
from agent.kernel.types import Taint
from agent.security.capabilities import gate_tool_call


def _gate(small, task, read_header: str, path: str):
    small.taint_tracker(task.id).trust_paths({"path": "work/c.txt"})
    small.taint_tracker(task.id).observe(read_header + "\n---\n1:04|alpha = 1\n", Taint.UNTRUSTED)
    return gate_tool_call(small, task, EDIT_LINES, {"path": path, "start": "1:04", "end": "1:04", "content": "alpha = 2"})


def test_the_absolute_path_of_a_file_already_read_is_that_file(small, task):
    ab = (Path(small.config.root) / "work" / "c.txt").as_posix()
    assert _gate(small, task, '{"ok": true, "path": "%s"}' % ab, ab).allowed


def test_an_absolute_path_outside_the_root_stays_untrusted(small, task):
    out = "/elsewhere/work/c.txt"
    d = _gate(small, task, '{"ok": true, "path": "%s"}' % out, out)
    assert not d.allowed and d.detail["untrusted_args"] == ["path"]
