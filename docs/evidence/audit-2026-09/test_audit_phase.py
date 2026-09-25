"""AUDIT-2026-09 evidence: `ctx.phase` is process-wide (DR1, width).

The control plane and the desktop app serve every request from ONE
AgentContext on a threadpool. draft_plan() sets ctx.phase = "plan" for the
whole context while it waits on the model (a minute or more locally), so a
DIFFERENT task running in a background thread has its writes refused as
"plan phase is read-only" for that whole window. Green = demonstration.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import make_config  # noqa: E402

from agent.context import AgentContext  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.plan.planner import draft_plan  # noqa: E402
from agent.security.capabilities import gate_tool_call  # noqa: E402


def test_F_planning_task_A_refuses_writes_of_running_task_B(tmp_path):
    ctx = AgentContext(make_config(tmp_path))
    (tmp_path / "work").mkdir()
    mk = lambda g: create_task(ctx.conn, ctx.log,  # noqa: E731
                               TaskContract(goal=g, artifact_scope=["work"]),
                               Budget(usd=0, seconds=60))
    a, b = mk("plan me"), mk("run me")
    in_model, release = threading.Event(), threading.Event()

    def slow_model(task, backend, purpose, **kw):
        in_model.set()
        release.wait(10)
        return SimpleNamespace(text='{"objective": "x", "steps": []}')

    ctx.call_model = slow_model
    th = threading.Thread(target=draft_plan, args=(ctx, a))
    th.start()
    assert in_model.wait(10)
    # Task B, executing, asks for an ordinary scoped write via the same
    # call the loop makes (loop.py:973 passes phase=ctx.phase).
    d = gate_tool_call(ctx, b, ctx.tools.get("write_file"),
                       {"path": "work/out.txt", "content": "hi"}, phase=ctx.phase)
    release.set()
    th.join(10)
    print(d)
    assert d.verdict == "deny" and "plan phase is read-only" in d.reason
    ctx.close()


import pytest  # noqa: E402

from agent.plan.planner import parse_plan  # noqa: E402


@pytest.mark.parametrize("text,exc", [
    ('{"objective":"o","steps":[{"est_tokens":"about 500"}]}', ValueError),
    ('{"objective":"o","steps":[{"action_mode":"python"}]}', ValueError),
    ('{"objective":"o","steps":[{"max_taint_out":"low"}]}', ValueError),
    ('{"objective":"o","steps":["read the file"]}', AttributeError),
    ('{"objective":"o","steps":[{"tools":5}]}', TypeError),
])
def test_F_plausible_planner_output_crashes_parse_plan(text, exc):
    """parse_plan degrades gracefully only when NO JSON is found. JSON a 3B
    model plausibly emits raises instead: POST /tasks/{id}/plan is a 500 and
    the exception text - carrying model output - goes to the server log
    (desktop.log in the windowed build)."""
    with pytest.raises(exc) as e:
        parse_plan(text, fallback_objective="goal")
    print(type(e.value).__name__, str(e.value)[:160].replace("\n", " "))


def test_F_planner_survey_reads_protected_state_into_the_prompt(tmp_path):
    """DR3: the capability gate refuses read_file('.alpha_token') - it is in
    PROJECT_STATE - but draft_plan's _read_only_survey reads every scope entry
    directly (planner.py:88-100), with no is_protected check, and pastes the
    first 2000 chars into the planner prompt. Scope is whatever the task
    declared: a console task, or an imported suite file."""
    ctx = AgentContext(make_config(tmp_path))
    (tmp_path / ".alpha_token").write_text("SENTINEL-TOKEN-1234", encoding="utf-8")
    t = create_task(ctx.conn, ctx.log,
                    TaskContract(goal="g", artifact_scope=[".alpha_token"]),
                    Budget(usd=0, seconds=60))
    gate = gate_tool_call(ctx, t, ctx.tools.get("read_file"), {"path": ".alpha_token"})
    assert gate.verdict == "deny"                   # the tool route is closed
    seen = {}

    def spy(task, backend, purpose, **kw):
        seen["tail"] = kw.get("tail", "")
        return SimpleNamespace(text='{"objective": "x", "steps": []}')

    ctx.call_model = spy
    draft_plan(ctx, t)
    assert "SENTINEL-TOKEN-1234" in seen["tail"]    # the survey route is open
    ctx.close()
