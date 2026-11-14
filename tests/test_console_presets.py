from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.config import BackendConfig
from agent.control import conversations
from agent.control.server import create_app
from agent.kernel.events import canonical, sha256_hex
from test_console_reskin_guards import UI, _calls
from test_local_models import FakeOllama

FAST = {"executor_model": "granite4.1:3b", "reviewer_model": "granite4.1:8b", "scope": "work",
        "budget_usd": 0.2, "budget_seconds": 300}


@pytest.fixture
def fake():
    f = FakeOllama()
    yield f
    f.close()


@pytest.fixture
def api(ctx, workspace, fake):
    from agent.models.ollama import OllamaBackend

    for slot, model in (("cloud", "granite4.1:8b"), ("review", "granite4.1:3b")):
        cfg = BackendConfig(kind="ollama", model=model, endpoint=fake.endpoint, model_server="ollama")
        ctx.config.backends[slot] = cfg
        ctx.backends[slot] = OllamaBackend(slot, cfg)
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_a_preset_is_saved_listed_replaced_and_deleted(api):
    assert api.put("/presets/Fast drafts", json=FAST).status_code == 200
    got = api.get("/presets").json()["presets"]
    assert [p["name"] for p in got] == ["Fast drafts"] and got[0]["body"] == FAST
    assert api.put("/presets/Fast drafts", json={**FAST, "budget_usd": 0.5}).status_code == 200
    assert api.get("/presets").json()["presets"][0]["body"]["budget_usd"] == 0.5
    assert api.delete("/presets/Fast drafts").status_code == 200
    assert api.get("/presets").json()["presets"] == []
    assert api.delete("/presets/Fast drafts").status_code == 404


@pytest.mark.parametrize("name,body", [
    ("../x", FAST), ("", FAST), ("x" * 41, FAST),
    ("ok", {**FAST, "scope": "../etc"}), ("ok", {**FAST, "scope": "/abs"}),
    ("ok", {**FAST, "budget_usd": -1}), ("ok", {**FAST, "budget_usd": 5000}),
    ("ok", {**FAST, "budget_seconds": 0}), ("ok", {**FAST, "executor_model": "a\nb"}),
    ("ok", {**FAST, "persona": "You are a pirate"}),
], ids=["path", "empty", "long", "scope-up", "scope-abs", "neg-usd", "huge-usd", "zero-s", "newline", "persona"])
def test_a_bad_preset_is_refused(api, name, body):
    assert api.put(f"/presets/{name}", json=body).status_code in (400, 404, 405, 422)


def test_applying_switches_both_models_and_returns_the_composer_fields(ctx, api):
    api.put("/presets/fast", json=FAST)
    r = api.post("/presets/fast/apply")
    assert r.status_code == 200, r.text
    assert ctx.backends["cloud"].cfg.model == "granite4.1:3b" and ctx.backends["review"].cfg.model == "granite4.1:8b"
    assert r.json()["scope"] == "work" and r.json()["budget_usd"] == 0.2


def test_a_preset_without_models_leaves_them_alone(ctx, api):
    api.put("/presets/scope-only", json={"scope": "work/docs", "budget_usd": None, "budget_seconds": None,
                                         "executor_model": None, "reviewer_model": None})
    assert api.post("/presets/scope-only/apply").status_code == 200
    assert ctx.backends["cloud"].cfg.model == "granite4.1:8b"


def test_applying_is_refused_while_a_task_runs_and_for_a_model_not_installed(ctx, api, monkeypatch):
    import agent.control.server as srv

    api.put("/presets/fast", json=FAST)
    api.put("/presets/ghost", json={**FAST, "executor_model": "nope:1b"})
    assert api.post("/presets/ghost/apply").status_code == 400
    assert ctx.backends["cloud"].cfg.model == "granite4.1:8b", "nothing switched when one model is missing"

    class Alive:
        def is_alive(self):
            return True

    monkeypatch.setitem(srv._runs, "t_busy", Alive())
    assert api.post("/presets/fast/apply").status_code == 409
    assert api.post("/presets/nothing/apply").status_code == 404


def test_a_task_records_the_preset_it_started_from_and_keeps_it_after_deletion(ctx, api):
    api.put("/presets/fast", json=FAST)
    c = conversations.create(ctx.conn, "x")
    tid = api.post(f"/conversations/{c['id']}/messages", json={"text": "do it", "preset": "fast"}).json()["task_id"]
    api.delete("/presets/fast")
    ev = [e["payload"] for e in ctx.log.events(tid) if e["type"] == "task.preset"]
    assert ev == [{"name": "fast", "sha256": sha256_hex(canonical(FAST))}]


def test_an_unknown_preset_on_a_message_is_refused_and_no_task_is_made(ctx, api):
    c = conversations.create(ctx.conn, "x")
    assert api.post(f"/conversations/{c['id']}/messages", json={"text": "do it", "preset": "nothing"}).status_code == 400
    assert ctx.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_the_page_saves_applies_and_drops_the_label_on_a_manual_change():
    calls = _calls(UI)
    assert {("GET", "/presets"), ("PUT", "/presets/{}"), ("DELETE", "/presets/{}"), ("POST", "/presets/{}/apply")} <= calls
    assert "preset: S.preset" in UI
    assert "S.preset = null" in UI
