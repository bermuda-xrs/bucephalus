from __future__ import annotations

from agent.execute.tools import EDIT_LINES
from agent.kernel.types import Taint
from agent.security.capabilities import gate_tool_call

READ = "1:04|alpha = 1\n2:9c|beta = 2\n"


def _gate(small, task, **args):
    small.taint_tracker(task.id).observe(READ, Taint.UNTRUSTED)
    return gate_tool_call(small, task, EDIT_LINES, {"path": "work/c.txt", "content": "beta = 3", **args})


def test_an_anchor_copied_from_the_read_output_passes_the_gate(small, task):
    d = _gate(small, task, start="2:9c", end="2:9c")
    assert d.allowed, d


def test_only_the_anchor_form_is_exempt(small, task):
    assert not _gate(small, task, start="2:9c", end="alpha = 1").allowed
    assert not _gate(small, task, start="2:9c", end="2:9c", content="alpha = 1").allowed
