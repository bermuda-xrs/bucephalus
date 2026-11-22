from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app
from test_console_reskin_guards import CONTROL, HTML, UI

CSS = re.sub(r"/\*.*?\*/", "", (CONTROL / "ui.css").read_text(encoding="utf-8"), flags=re.S)
NODE = shutil.which("node")


def _node(script: str):
    if NODE is None:
        pytest.skip("node is not installed")
    src = UI[UI.index("function statusWord("):UI.index("async function applyTheme(")]
    return json.loads(subprocess.run([NODE, "-e", src + script], capture_output=True, text=True, check=True,
                                     encoding="utf-8").stdout)


def _fn(name: str) -> str:
    i = UI.index(f"function {name}(")
    return UI[i:UI.index("\n}", i)]


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_the_run_list_carries_what_the_rail_shows(ctx, api):
    from agent.kernel.types import Budget

    c = api.post("/conversations", json={}).json()
    a = api.post(f"/conversations/{c['id']}/messages", json={"text": "list the files"}).json()
    loose = api.post("/tasks", json={"goal": "from a schedule", "artifact_scope": ["work"]}).json()
    ctx.conn.execute("UPDATE tasks SET budget_json=? WHERE id=?",
                     (Budget(usd=1.0, seconds=60, spent_usd=0.25).model_dump_json(), a["task_id"]))
    ctx.conn.execute("INSERT INTO approvals(id, task_id, step_id, tool, args_json, preview, reason, status, created_at)"
                     " VALUES ('a_1', ?, '2', 'run_command', '{}', 'cmd=ls', 'r', 'pending', 1)", (a["task_id"],))
    ctx.conn.commit()
    rows = {r["task_id"]: r for r in api.get("/tasks").json()["tasks"]}
    mine, other = rows[a["task_id"]], rows[loose["task_id"]]
    assert (mine["conversation_id"], mine["conversation"], mine["pending"], mine["spent_usd"]) == (
        c["id"], "list the files", 1, 0.25)
    assert mine["archived"] is False and mine["pinned"] is False and mine["updated_at"] >= mine["created_at"]
    assert (other["conversation_id"], other["pending"], other["spent_usd"]) == (None, 0, 0.0)


def test_measurement_runs_stay_out_of_the_rail(ctx, api):
    mine = api.post("/tasks", json={"goal": "count the files", "artifact_scope": ["work"]}).json()["task_id"]
    bench = api.post("/tasks", json={"goal": "suite task", "artifact_scope": ["work"]}).json()["task_id"]
    legacy = api.post("/tasks", json={"goal": "old suite task [arm:B]", "artifact_scope": ["work"]}).json()["task_id"]
    ctx.conn.execute("UPDATE tasks SET origin='bench' WHERE id=?", (bench,))
    ctx.conn.commit()
    ids = {r["task_id"] for r in api.get("/tasks?bench=false").json()["tasks"]}
    assert mine in ids and bench not in ids and legacy not in ids
    assert {mine, bench, legacy} <= {r["task_id"] for r in api.get("/tasks").json()["tasks"]}, "Files still lists them"
    assert "bench=false" in _fn("loadSide")


def test_what_waits_is_listed_however_many_runs_came_after_it(ctx, api):
    old = api.post("/tasks", json={"goal": "waiting for a signature", "artifact_scope": ["work"]}).json()["task_id"]
    for i in range(4):
        t = api.post("/tasks", json={"goal": f"filler {i}", "artifact_scope": ["work"]}).json()["task_id"]
        ctx.conn.execute("UPDATE tasks SET status='done' WHERE id=?", (t,))
    ctx.conn.commit()
    get = lambda q: [r["task_id"] for r in api.get("/tasks?limit=2" + q).json()["tasks"]]  # noqa: E731
    assert old not in get("") and get("&live=true") == [old]
    assert "live=true" in _fn("loadSide")
    out = _node("""process.stdout.write(JSON.stringify(
      merged([{task_id: "a"}, {task_id: "b"}], [{task_id: "c"}, {task_id: "a"}]).map(t => t.task_id)));""")
    assert out == ["a", "b", "c"]


def test_search_and_archive_reach_past_the_newest_runs(ctx, api):
    c = api.post("/conversations", json={}).json()
    old = api.post(f"/conversations/{c['id']}/messages", json={"text": "rename the fixtures"}).json()["task_id"]
    api.patch(f"/conversations/{c['id']}", json={"archived": True, "title": "Fixture work"})
    for i in range(5):
        api.post("/tasks", json={"goal": f"filler {i}", "artifact_scope": ["work"]})
    get = lambda q: [r["task_id"] for r in api.get("/tasks?limit=3" + q).json()["tasks"]]  # noqa: E731
    assert old not in get("")
    assert get("&q=fixtures") == [old] and get("&q=fixture%20work") == [old]
    assert get("&archived=true") == [old] and old not in get("&archived=false&q=fixtures")


def test_the_rail_survives_what_it_cannot_name_and_a_lost_connection():
    side = _fn("loadSide")
    assert '|| "A delegated task"' in side
    assert '$("#health").hidden = true' in side
    assert "&archived=${S.archived}" in side and "&q=${encodeURIComponent(" in side
    assert "!t.conversation_id" in _fn("openLooseList")


def test_the_queue_has_a_decision_tier_and_a_review_tier():
    out = _node("""
      const tasks = [
        {task_id: "p", status: "pending", plan_status: "drafted", updated_at: 50},
        {task_id: "r", status: "running", plan_status: "approved", updated_at: 50},
        {task_id: "d", status: "done", plan_status: "approved", updated_at: 50},
        {task_id: "f", status: "failed", plan_status: "approved", updated_at: 50},
        {task_id: "old", status: "done", plan_status: "approved", updated_at: 5},
        {task_id: "seen", status: "done", plan_status: "approved", updated_at: 50},
        {task_id: "shelved", status: "done", plan_status: "approved", updated_at: 50, archived: true}];
      const gates = [{id: "g1", task_id: "r", tool: "run_command"}];
      const t = tiers(tasks, gates, {since: 10, ids: new Set(["seen"])});
      process.stdout.write(JSON.stringify({decide: t.decide.map(x => x.kind + ":" + x.task_id),
                                           review: t.review.map(x => x.task_id)}));""")
    assert out == {"decide": ["approval:r", "plan:p"], "review": ["d", "f"]}


def test_runs_are_grouped_by_status():
    out = _node("""
      const tasks = ["running", "paused", "pending", "done", "done_unannounced", "failed", "budget_exhausted",
                     "rejected", "done"].map((s, i) => ({task_id: "t" + i, status: s, pinned: i === 8}));
      process.stdout.write(JSON.stringify(runGroups(tasks).map(([g, xs]) => [g, xs.map(x => x.task_id)])));""")
    assert out == [["Live", ["t0", "t1", "t2"]], ["Done", ["t8", "t3", "t4"]], ["Stopped", ["t5", "t6", "t7"]]]
    assert "function dayGroup(" not in UI


def test_an_approval_says_whether_the_signed_plan_expected_it():
    out = _node("""
      const plan = {human_approvals_expected: ["3"], steps: [
        {id: "1", tools: ["read_file"]}, {id: "2", tools: ["write_file"], reversible: false},
        {id: "3", tools: ["run_command"], reversible: true}]};
      process.stdout.write(JSON.stringify([expectedStop(plan, "run_command"), expectedStop(plan, "write_file"),
        expectedStop(null, "run_command"), mustType(plan, "write_file"), mustType(plan, "run_command"),
        mustType(null, "x")]));""")
    assert out == [3, 0, 0, True, False, False]


def test_the_approval_card_shows_the_effect_first_and_never_preselects_allow():
    card = _fn("approvalCard")
    assert card.index("g.preview") < card.index("g.reason")
    assert "expectedStop(" in card and "mustType(" in card and "data-confirm" in card
    assert "autofocus" not in UI + HTML and ".focus()" not in card
    decide = _fn("decide")
    assert "data-confirm" in decide or "confirmed(" in decide


def test_while_the_record_does_not_verify_nothing_can_be_signed_or_allowed():
    assert "S.unsafe = " in _fn("loadAlerts")
    for name in ("sign", "decide"):
        assert re.search(r"if \(S\.unsafe\b", _fn(name)), name
    for name in ("approvalCard", "planCard"):
        assert "S.unsafe ? " in _fn(name), name


def test_the_integrity_alarm_is_never_behind_a_page_or_a_drawer():
    thread = HTML[HTML.index('<main class="thread"'):HTML.index("</main>")]
    assert 'id="integrity"' not in thread, "a page hides the thread and the alarm with it"
    assert re.search(r'<div class="app[^>]*>\s*<div id="integrity" class="integrity" role="alert" hidden></div>', HTML)
    assert re.search(r"#integrity \{[^}]*grid-column: 1 / -1; grid-row: 1", CSS)
    assert re.search(r"\.side, \.thread, \.trace, \.files, \.page \{[^}]*grid-row: 2", CSS)
    drawers = re.findall(r"\n\s*\.(?:side|trace, \.files) \{([^}]*)\}", CSS)
    assert drawers and not any("position: fixed" in d for d in drawers), "a fixed drawer covers the alarm"


def test_opening_a_page_leaves_the_way_back_in_view():
    pane = _fn("openPane")
    assert "body.focus({preventScroll: true})" in pane and '$("#control").scrollTop = 0' in pane


def test_only_decisions_notify_and_only_after_a_hold():
    notify_needs = _fn("notifyNeeds")
    assert "HOLD_MS" in notify_needs and "setTimeout(" in notify_needs
    assert "await loadSide()" in notify_needs, "the recheck must read fresh state, not the last poll's"
    assert re.search(r"const HOLD_MS = 5000;", UI)
    assert 'notify("A task finished"' not in UI


def test_three_columns_at_most_and_no_page_rail():
    grid = re.search(r"\.app \{[^}]*grid-template-columns:\s*([^;]+);", CSS).group(1)
    assert len(re.findall(r"var\(--\w+\)|minmax\([^)]*\)", grid)) == 3, grid
    assert 'class="nav' not in HTML and "nav-item" not in HTML + CSS
    pages = re.search(r'<nav class="pages"[^>]*>(.*?)</nav>', HTML, re.S).group(1)
    assert re.findall(r"<summary>(\w+)</summary>", pages) == ["Workspace", "Settings", "System"]
    assert set(re.findall(r'data-pane="(\w+)"', HTML)) == set(re.findall(r'data-pane="(\w+)"', pages))


def test_trace_and_files_share_one_inspector():
    assert re.search(r"\.trace, \.files \{[^}]*grid-column: 3", CSS)
    files = re.search(r"\n\.files \{([^}]*)\}", CSS).group(1)
    assert "position: fixed" not in files
    assert re.search(r"\.app:not\(\.files-closed\) \.trace \{[^}]*display: none", CSS)


def test_the_run_settings_sit_behind_one_disclosure():
    box = re.search(r'<details class="run-settings">(.*?)</details>', HTML, re.S).group(1)
    for el in ('id="modelPick"', 'id="chipProfile"', 'id="scope"', 'id="budUsd"'):
        assert el in box, el


def test_quiet_when_normal():
    assert '$("#ringWrap").hidden = ' in _fn("updateRing")
    assert '$("#health").hidden = true' in UI
    assert re.search(r'<p class="health" id="health" hidden>', HTML)


def test_the_run_page_folds_the_other_runs_of_its_conversation():
    items = _fn("renderItems")
    assert "S.run" in items and 'class="earlier"' in items and "markSeen(" in items
    assert "waiting(x)" in items, "a run that needs you is never folded away"
    out = _node("""process.stdout.write(JSON.stringify([waiting({approvals: [{}], status: "paused"}),
      waiting({approvals: [], status: "pending", plan: {status: "drafted"}}),
      waiting({approvals: [], status: "done", plan: {status: "approved"}})]));""")
    assert out == [True, True, False]
    assert "S.run = taskId" in _fn("openTaskOf")
    side = _fn("loadSide")
    assert 'api("/tasks' in side and "tiers(" in side and "runGroups(" in side
