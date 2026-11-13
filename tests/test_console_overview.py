from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from agent.control import conversations
from agent.control.server import TRACE_EVENTS, create_app
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from test_console_reskin_guards import HTML, UI, _calls

NODE = shutil.which("node")


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def _task(ctx, goal="g", parent=None, origin="local"):
    t = create_task(ctx.conn, ctx.log, TaskContract(goal=goal, artifact_scope=["work"]), Budget(usd=1.0, seconds=60),
                    parent_id=parent, origin=origin)
    return t.id


def test_the_overview_groups_tasks_by_conversation_in_order(ctx, api):
    conv = conversations.create(ctx.conn, "Refactor")
    a = conversations.add_message(ctx, conv["id"], "first ask", ["work"], None, None)["task_id"]
    b = conversations.add_message(ctx, conv["id"], "follow-up", ["work"], None, None)["task_id"]
    loose = _task(ctx, "a task from the CLI")
    d = api.get("/graph/overview").json()
    got = {c["id"]: c for c in d["conversations"]}
    assert got[conv["id"]]["title"] == "Refactor" and got[conv["id"]]["tasks"] == [a, b]
    tasks = {t["id"]: t for t in d["tasks"]}
    assert tasks[a]["conversation_id"] == conv["id"] and tasks[loose]["conversation_id"] is None
    assert tasks[a]["goal"] == "first ask" and tasks[a]["status"] == "pending"


def test_a_delegated_task_keeps_its_parent(ctx, api):
    p = _task(ctx, "parent")
    c = _task(ctx, "child", parent=p)
    tasks = {t["id"]: t for t in api.get("/graph/overview").json()["tasks"]}
    assert tasks[c]["parent_id"] == p


def test_measurement_tasks_are_left_out(ctx, api):
    _task(ctx, "bench arm", origin="bench")
    keep = _task(ctx, "real")
    assert [t["id"] for t in api.get("/graph/overview").json()["tasks"]] == [keep]


def test_only_the_trace_allowlist_is_sent_and_long_text_is_cut(ctx, api):
    t = _task(ctx)
    ctx.log.append(t, "model.called", {"purpose": "step", "step": 0, "est_input_tokens": 10})
    ctx.log.append(t, "model.thought", {"thought": "x" * 5000, "carry": ""})
    ctx.log.append(t, "tool.intent", {"tool": "read_file", "step": 0, "key": "k", "preview": "y" * 5000})
    evs = {x["id"]: x for x in api.get("/graph/overview").json()["tasks"]}[t]["events"]
    assert {e["type"] for e in evs} <= set(TRACE_EVENTS)
    assert "task.created" not in {e["type"] for e in evs}
    assert all(len(v) <= 200 for e in evs for v in e["data"].values() if isinstance(v, str))
    assert [e["type"] for e in evs] == ["model.called", "model.thought", "tool.intent"]


def test_the_limit_keeps_the_most_recent_tasks(ctx, api):
    ids = [_task(ctx, f"t{i}") for i in range(5)]
    got = [t["id"] for t in api.get("/graph/overview?limit=2").json()["tasks"]]
    assert sorted(got) == sorted(ids[-2:])
    assert api.get("/graph/overview?limit=0").status_code == 422
    assert api.get("/graph/overview?limit=999").status_code == 422


def test_the_overview_needs_the_token(ctx, workspace):
    assert TestClient(create_app(ctx), base_url="http://127.0.0.1").get("/graph/overview").status_code == 401


def test_the_page_calls_its_route_and_has_a_key():
    assert ("GET", "/graph/overview") in _calls(UI)
    assert 'data-pane="map"' in HTML
    block = UI[UI.index("async map()"):]
    block = block[:block.index("\n  async ")]
    assert '<details class="fg-help">' in block
    assert 'if (name === "map") drawMap();' in UI


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_map_is_a_tree_of_sessions_with_each_task_on_a_rail():
    src = UI[UI.index("const MAX_GRAPH_NODES"):UI.index("function fitGraph(")] + UI[UI.index("const MAP_COL"):UI.index("const MAP_HELP")]
    script = src + """
const steps = n => [{kind: "step", step: 0, label: "step 1"}, {kind: "tool", label: "read_file"},
                    ...(n > 1 ? [{kind: "step", step: 1, label: "step 2"}] : [])];
const data = {conversations: [{id: "c1", title: "One", tasks: ["a", "b"]}],
              tasks: [{id: "a", goal: "A", status: "done", parent_id: null, conversation_id: "c1", nodes: steps(2)},
                      {id: "b", goal: "B", status: "done", parent_id: null, conversation_id: "c1", nodes: steps(1)},
                      {id: "x", goal: "X", status: "done", parent_id: "a", conversation_id: null, nodes: steps(1)}]};
process.stdout.write(JSON.stringify(mapLayout(data)));"""
    out = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout)
    nodes = {n["id"]: n for n in out["nodes"]}
    links = {(lk["source"], lk["target"], lk.get("kind")) for lk in out["links"]}
    root, s1, s0 = nodes["root"], nodes["s:c1"], nodes["s:"]
    assert root["fy"] < s1["fy"] and root["fy"] < s0["fy"] and s1["fx"] != s0["fx"]
    ta, tb, tx = nodes["t:a"], nodes["t:b"], nodes["t:x"]
    assert ta["fx"] == tb["fx"] < nodes["a:n0"]["fx"] and ta["fy"] < tb["fy"]
    assert tx["col"] != ta["col"] and tx["fx"] == s0["fx"], "a task outside a conversation sits in the last column"
    assert nodes["a:n0"]["fy"] < nodes["a:n2"]["fy"] < tb["fy"], "a task's spine sits between it and the next task"
    assert nodes["a:n1"]["fx"] < nodes["a:n0"]["fx"], "calls left of their step"
    assert all(nodes[i]["detail"] for i in ("a:n0", "a:n1", "b:n0")) and not ta.get("detail") and not s1.get("detail")
    assert ("root", "s:c1", "tree") in links and ("s:c1", "t:a", "tree") in links and ("t:a", "t:b", "tree") in links
    assert ("t:a", "a:n0", None) in links and ("t:a", "t:x", "delegated") in links
    assert all(isinstance(n["fx"], (int, float)) and isinstance(n["fy"], (int, float)) for n in out["nodes"])
