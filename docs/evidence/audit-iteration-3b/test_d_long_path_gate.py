"""D70 regression: _protected_elsewhere calls os.stat, which raises ValueError
(not OSError) for a path over 32,767 characters on Windows. The gate used to
answer; now it raises, and a run whose model emits such a path crashes.
See probe_e_long_path_gate.py for the before/after comparison."""

from __future__ import annotations

import sys

import pytest

from agent.execute.loop import run_task
from agent.security.capabilities import gate_tool_call, is_protected

LONG = "work/" + "b/" * 20000 + "c.txt"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows path limit")


def test_FINDING_is_protected_raises_on_an_overlong_path(tmp_path):
    with pytest.raises(ValueError, match="too long"):
        is_protected(LONG, str(tmp_path))


def test_FINDING_the_gate_raises_instead_of_deciding(ctx, task):
    with pytest.raises(ValueError):
        gate_tool_call(ctx, task, ctx.tools.get("read_file"), {"path": LONG})


def test_FINDING_a_model_emitting_an_overlong_path_crashes_the_run(ctx, task, scripted):
    scripted("cloud", *[f'<action tool="read_file">{{"path": "{LONG}"}}</action>'] * 6)
    res = run_task(ctx, task.id)
    types = [r[0] for r in ctx.conn.execute(
        "SELECT type FROM events WHERE task_id=? ORDER BY seq", (task.id,)).fetchall()]
    crashed = ctx.conn.execute("SELECT payload_json FROM events WHERE task_id=? AND type='task.crashed'",
                               (task.id,)).fetchone()
    print(f"\nstatus={res.status} crashed={crashed[0][:120] if crashed else None}")
    assert crashed is not None and "ValueError" in crashed[0]
    assert "human.denied" not in types
