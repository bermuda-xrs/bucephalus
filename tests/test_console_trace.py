from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control.server import TRACE_EVENTS, create_app
from agent.execute.loop import run_task

CONTROL = Path(__file__).resolve().parents[1] / "src" / "agent" / "control"
STEP = ('{"kind": "action", "tool": "list_dir", "args": {"path": "work"}, '
        '"thought": "look before answering", "carry": "work/ holds the notes"}')
DONE = '{"kind": "final", "answer": {"answer": "done"}}'


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_the_trace_has_each_step_its_thought_and_its_tool_call_in_order(ctx, task, scripted, api):
    scripted("cloud", STEP, DONE)
    assert run_task(ctx, task.id).status.startswith("done")
    trace = api.get(f"/tasks/{task.id}/trace").json()
    types = [e["type"] for e in trace]
    thought = next(e["data"] for e in trace if e["type"] == "model.thought")
    assert thought == {"step": 0, "thought": "look before answering", "carry": "work/ holds the notes"}
    assert types.index("model.called") < types.index("model.thought") < types.index("tool.intent") \
        < types.index("tool.receipt") < len(types) - 1
    assert types[-1] == "task.status_changed"
    assert [e["seq"] for e in trace] == sorted(e["seq"] for e in trace)


def test_a_step_with_nothing_to_say_logs_no_thought(ctx, task, scripted, api):
    scripted("cloud", '{"kind": "action", "tool": "list_dir", "args": {"path": "work"}}', DONE)
    run_task(ctx, task.id)
    assert not [e for e in api.get(f"/tasks/{task.id}/trace").json() if e["type"] == "model.thought"]


def test_the_trace_names_real_events_and_never_the_monitor():
    from agent.kernel.events import EVENT_TYPES

    assert TRACE_EVENTS and set(TRACE_EVENTS) <= EVENT_TYPES, set(TRACE_EVENTS) - EVENT_TYPES
    assert not [t for t in TRACE_EVENTS if t.startswith("monitor.")]


def test_the_page_offers_each_view_and_draws_the_graph_without_inline_style():
    ui, html = (CONTROL / "ui.js").read_text(encoding="utf-8"), (CONTROL / "ui.html").read_text(encoding="utf-8")
    views = set(re.findall(r'data-view="(\w+)"', html))
    assert views == {"normal", "thinking", "verbose", "graph"}, views
    assert all(f"{v}:" in ui[ui.index("const VIEWS = {"):] for v in views)
    assert "<svg" in ui and "marker" in ui
