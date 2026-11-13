from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from agent.control import conversations
from agent.control.server import create_app
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from test_console_reskin_guards import HTML, UI, _calls

NODE = shutil.which("node")
MAP_SRC = UI[UI.index("const MAX_GRAPH_NODES"):UI.index("function fitGraph(")] + UI[UI.index("const MAP_COL"):UI.index("const MAP_HELP")]


def _node(script: str):
    out = subprocess.run([NODE, "-e", MAP_SRC + script], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


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


def test_the_overview_is_an_outline_with_step_counts_and_no_events(ctx, api):
    t = _task(ctx)
    for s_ in range(3):
        ctx.log.append(t, "model.called", {"purpose": "step", "step": s_, "est_input_tokens": 10})
    ctx.log.append(t, "model.called", {"purpose": "review"})
    ctx.log.append(t, "model.called", {"step": 3})
    ctx.log.append(t, "tool.intent", {"tool": "read_file", "step": 0, "key": "k", "preview": "secret-ish preview"})
    r = api.get("/graph/overview")
    row = {x["id"]: x for x in r.json()["tasks"]}[t]
    assert row["steps"] == 4, "steps, and calls with no purpose, count; review calls do not"
    assert "events" not in row and "secret-ish" not in r.text
    assert r.json()["truncated"] is False


def test_the_limit_keeps_the_most_recent_tasks_and_says_when_it_cut(ctx, api):
    ids = [_task(ctx, f"t{i}") for i in range(5)]
    d = api.get("/graph/overview?limit=2").json()
    assert sorted(t["id"] for t in d["tasks"]) == sorted(ids[-2:]) and d["truncated"] is True
    assert api.get("/graph/overview").json()["truncated"] is False
    assert api.get("/graph/overview?limit=0").status_code == 422
    assert api.get("/graph/overview?limit=5001").status_code == 422
    assert api.get("/graph/overview?limit=5000").status_code == 200


def test_the_overview_needs_the_token(ctx, workspace):
    assert TestClient(create_app(ctx), base_url="http://127.0.0.1").get("/graph/overview").status_code == 401


def test_the_page_calls_its_routes_and_has_a_key():
    calls = _calls(UI)
    assert {("GET", "/graph/overview"), ("GET", "/tasks/{}/trace"), ("GET", "/tasks/{}/diff")} <= calls
    assert 'data-pane="map"' in HTML
    block = UI[UI.index("async map()"):]
    block = block[:block.index("\n  async ")]
    assert '<details class="fg-help">' in block and 'tabindex="0"' in block and 'aria-live="polite"' in block
    assert 'if (name === "map") drawMap();' in UI
    drop = UI[UI.index("function dropMap("):]
    assert "clearInterval(" in drop[:drop.index("\n}\n")]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_map_is_a_tree_of_sessions_with_each_task_on_a_rail():
    src = MAP_SRC
    script = src + """
const steps = n => [{kind: "step", step: 0, label: "step 1"}, {kind: "tool", label: "read_file"},
                    ...(n > 1 ? [{kind: "step", step: 1, label: "step 2"}] : [])];
const data = {conversations: [{id: "c1", title: "One", tasks: ["a", "b"]}],
              tasks: [{id: "a", goal: "A", status: "done", parent_id: null, conversation_id: "c1", steps: 2},
                      {id: "b", goal: "B", status: "done", parent_id: null, conversation_id: "c1", steps: 1},
                      {id: "x", goal: "X", status: "done", parent_id: "a", conversation_id: null, steps: 1}]};
const details = new Map([["a", {nodes: steps(2)}], ["b", {nodes: steps(1)}], ["x", {nodes: steps(1)}]]);
process.stdout.write(JSON.stringify(mapLayout(data, details)));"""
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


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_task_not_loaded_yet_keeps_the_room_its_steps_will_need():
    out = _node("""
const data = {conversations: [{id: "c", title: "C", tasks: ["a", "b"]}],
              tasks: [{id: "a", goal: "A", status: "done", conversation_id: "c", steps: 10},
                      {id: "b", goal: "B", status: "running", conversation_id: "c", steps: 2}]};
const empty = mapLayout(data, new Map());
const full = mapLayout(data, new Map([["a", {nodes: Array.from({length: 10}, (_, i) => ({kind: "step", step: i, label: "s"}))
  .concat([{kind: "end", label: "Done"}])}]]));
const y = (L, id) => L.nodes.find(n => n.id === id).fy;
process.stdout.write(JSON.stringify({b0: y(empty, "t:b"), b1: y(full, "t:b"), blocks: empty.blocks,
  details: empty.nodes.filter(n => n.detail).length}));""")
    assert out["b0"] == out["b1"], "loading a task's steps never moves the tasks below it"
    assert out["details"] == 0
    a = next(b for b in out["blocks"] if b["task"] == "a")
    assert a["y1"] - a["y0"] >= 11 * 36 and a["x0"] < a["x1"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_files_sit_in_the_right_column_without_overlapping():
    out = _node("""
const nodes = [0, 1, 2].map(i => ({kind: "step", step: i, label: "s"}));
const diffs = [{step: 1, files: ["w/a.py", "w/b.py", "w/c.py", "w/d.py"]}];
const L = mapLayout({conversations: [], tasks: [{id: "t", goal: "T", status: "done", conversation_id: null, steps: 3}]},
                    new Map([["t", {nodes, diffs}]]));
process.stdout.write(JSON.stringify(L.nodes.filter(n => n.kind === "file").map(n => [n.fx, n.fy])));""")
    xs, ys = {x for x, _ in out}, sorted(y for _, y in out)
    assert len(out) == 4 and len(xs) == 1 and next(iter(xs)) > 0
    assert all(b - a >= 16 for a, b in zip(ys, ys[1:]))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_only_tasks_in_view_are_asked_for():
    out = _node("""
const blocks = [{task: "a", x0: 0, x1: 100, y0: 0, y1: 100}, {task: "b", x0: 0, x1: 100, y0: 500, y1: 600},
                {task: "c", x0: 900, x1: 1000, y0: 0, y1: 100}];
process.stdout.write(JSON.stringify([mapVisible(blocks, {x0: 50, x1: 200, y0: 50, y1: 550}),
                                     mapVisible(blocks, {x0: 2000, x1: 3000, y0: 0, y1: 10})]));""")
    assert out == [["a", "b"], []]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_eviction_drops_the_longest_unseen_tasks_never_the_ones_in_view():
    out = _node("""
const m = new Map([["old", {nodes: new Array(40), seen: 1}], ["mid", {nodes: new Array(40), seen: 2}],
                   ["inview", {nodes: new Array(40), seen: 0}], ["new", {nodes: new Array(40), seen: 3}]]);
const gone = mapEvict(m, new Set(["inview"]), 100);
process.stdout.write(JSON.stringify({gone, kept: [...m.keys()].sort()}));""")
    assert out["gone"] == ["old", "mid"] and out["kept"] == ["inview", "new"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_arrow_keys_walk_the_tree():
    out = _node("""
const ns = [{id: "root", kind: "root", fx: 100, fy: -108},
            {id: "s:1", kind: "session", col: 0, fx: -150, fy: 0}, {id: "t:a", kind: "task", col: 0, fx: -150, fy: 72},
            {id: "t:b", kind: "task", col: 0, fx: -150, fy: 300},
            {id: "s:2", kind: "session", col: 1, fx: 310, fy: 0}, {id: "t:c", kind: "task", col: 1, fx: 310, fy: 80}];
const go = (from, key) => mapStep(ns, from, key, false);
process.stdout.write(JSON.stringify([go(null, "ArrowDown"), go("root", "ArrowDown"), go("s:1", "ArrowDown"),
  go("t:a", "ArrowDown"), go("t:b", "ArrowDown"), go("t:a", "ArrowUp"), go("s:1", "ArrowUp"),
  go("t:a", "ArrowRight"), go("t:c", "ArrowLeft"), go("t:b", "Home")]));""")
    assert out == ["root", "s:1", "t:a", "t:b", "t:b", "s:1", "root", "t:c", "t:a", "root"]
