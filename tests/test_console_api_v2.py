"""The console's new API (iteration 3, 4.2-4.3): conversations, the context
meter, and the Control Center's panes. Every route is behind the token."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def _draft(ctx, task_id):
    from agent.plan.types import Plan, PlanStep

    from agent.kernel.tasks import load_task
    from agent.plan.planner import store_plan

    # Stored the way the planner stores a draft: a fixture that spelled the
    # status by hand ("draft", where the planner writes "drafted") let the
    # console test green while it never offered a real plan for signature.
    plan = store_plan(ctx, load_task(ctx.conn, task_id),
                      Plan(objective="o", steps=[PlanStep(id="1", intent="read it")]).seal())


def test_a_message_is_a_new_task_in_its_conversation(ctx, api):
    c = api.post("/conversations", json={}).json()
    a = api.post(f"/conversations/{c['id']}/messages", json={"text": "list the files"}).json()
    b = api.post(f"/conversations/{c['id']}/messages", json={"text": "now count them"}).json()
    assert a["task_id"] != b["task_id"] and (a["position"], b["position"]) == (0, 1)
    th = api.get(f"/conversations/{c['id']}").json()
    assert [i["goal"] for i in th["items"]] == ["list the files", "now count them"]
    assert th["conversation"]["title"] == "list the files"       # named by its first ask
    assert th["items"][0]["scope"] == ["work"]
    assert api.get(f"/tasks/{a['task_id']}").json()["conversation_id"] == c["id"]
    # Nothing ran: a message waits for a plan and a signature.
    assert all(i["status"] == "pending" and i["plan"] is None for i in th["items"])


def test_the_thread_carries_the_plan_the_approvals_and_the_answer(ctx, api):
    from agent.ledger.artifacts import write_artifact
    from agent.kernel.types import Taint

    c = api.post("/conversations", json={"title": "t"}).json()
    tid = api.post(f"/conversations/{c['id']}/messages", json={"text": "x"}).json()["task_id"]
    _draft(ctx, tid)
    write_artifact(ctx.conn, tid, "answer", "42", Taint.TRUSTED, {"source": "final"})
    item = api.get(f"/conversations/{c['id']}").json()["items"][0]
    assert item["plan"]["status"] == "drafted" and item["plan"]["body"]["objective"] == "o"
    assert item["answer"] == "42" and item["approvals"] == []
    assert api.get("/conversations").json()["conversations"][0]["needs_you"] == 1


def test_conversations_are_searchable_and_archivable(api):
    a = api.post("/conversations", json={"title": "flight loads"}).json()
    b = api.post("/conversations", json={"title": "other"}).json()
    api.post(f"/conversations/{b['id']}/messages", json={"text": "compute the thrust margin"})
    ids = lambda **kw: [x["id"] for x in api.get("/conversations", params=kw).json()["conversations"]]
    assert ids(q="loads") == [a["id"]]
    assert ids(q="thrust") == [b["id"]]            # matches a goal in the thread
    api.patch(f"/conversations/{a['id']}", json={"archived": True})
    assert a["id"] not in ids() and ids(archived=True) == [a["id"]]
    assert api.post(f"/conversations/{a['id']}/messages", json={"text": "  "}).status_code == 400


def test_the_context_meter_shows_the_window_and_the_ceiling(ctx, api):
    m = api.get("/context").json()
    assert m["window"] == ctx.window_for("cloud")
    assert m["ceiling"] == int(m["window"] * ctx.config.limits.context_fill_ratio)
    assert m["ceiling"] < m["window"] and m["used"] == 0


def test_memory_is_shown_with_its_blessing_and_can_be_accepted(ctx, api):
    ctx.memory.root.mkdir(parents=True, exist_ok=True)
    (ctx.memory.root / "MEMORY.md").write_text("- [user] deploys go through staging\n",
                                               encoding="utf-8")
    m = api.get("/memory").json()
    texts = [e["text"] for t in m["tiers"] for e in t["entries"]]
    assert "deploys go through staging" in texts
    assert m["blessing"] != "intact"
    assert api.post("/memory/accept").json()["blessing"] == "intact"
    assert api.get("/memory").json()["blessing"] == "intact"


@pytest.mark.parametrize("name", ["schedules", "channels", "integrity", "measurement",
                                  "settings", "logs"])
def test_every_panel_answers(api, name):
    r = api.get(f"/panels/{name}")
    assert r.status_code == 200, r.text


def test_the_drop_log_never_carries_a_strangers_text(ctx, api):
    from agent.channel.telegram import record_drop

    record_drop(ctx.conn, "telegram", "999", "not on the allowlist", "run rm -rf / now")
    drops = api.get("/panels/channels").json()["drops"]
    assert drops and "rm -rf" not in json.dumps(drops)
    assert set(drops[0]) >= {"body_sha256", "body_len"}


@pytest.mark.parametrize("method,path", [
    ("GET", "/conversations"), ("POST", "/conversations"), ("GET", "/context"),
    ("GET", "/memory"), ("POST", "/memory/accept"), ("GET", "/panels/logs"),
    ("GET", "/tasks/x/files"), ("POST", "/tasks/x/files")])
def test_every_new_route_needs_the_token(ctx, workspace, method, path):
    c = TestClient(create_app(ctx), base_url="http://127.0.0.1")
    assert c.request(method, path, json={}).status_code == 401
