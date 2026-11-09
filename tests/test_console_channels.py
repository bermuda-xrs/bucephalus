from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.channel.telegram import FakeTransport, TelegramBot, record_drop
from agent.control.server import create_app
from agent.kernel.tasks import load_task
from agent.security.origin import TELEGRAM
from test_console_reskin_guards import HTML, UI, _calls

CONFIGURED = "424242"
NEW = "777001"


@pytest.fixture
def api(ctx, workspace):
    ctx.config.channels.telegram.enabled = True
    ctx.config.channels.telegram.allowed_senders = [CONFIGURED]
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def msg(sender, text="count the files", uid=1):
    return {"update_id": uid, "message": {"message_id": uid, "from": {"id": int(sender)},
                                          "chat": {"id": 7000}, "text": text}}


def allow(api, sender=NEW, confirm=None):
    return api.post("/channels/telegram/allow", json={"sender": sender, "confirm": sender if confirm is None else confirm})


def test_allowing_a_sender_needs_the_id_typed_back(api):
    assert allow(api, confirm="").status_code == 400
    assert allow(api, confirm="777002").status_code == 400
    assert NEW not in str(api.get("/panels/channels").json()["allowed"])
    assert allow(api).status_code == 200


def test_only_a_telegram_id_is_accepted(api):
    for bad in ("", "abc", "12 34", "-5", "1" * 30, "1;DROP"):
        assert allow(api, bad).status_code == 400, bad


def test_the_page_lists_where_each_sender_came_from(api):
    allow(api)
    got = {a["sender"]: a["source"] for a in api.get("/panels/channels").json()["allowed"]}
    assert got == {CONFIGURED: "config", NEW: "console"}


def test_a_newly_allowed_sender_is_heard_and_stays_a_channel_task(ctx, api):
    bot = TelegramBot(ctx, FakeTransport())
    assert bot.handle_update(msg(NEW))["action"] == "dropped"
    allow(api)
    out = bot.handle_update(msg(NEW, uid=2))
    assert out["action"] == "created"
    assert load_task(ctx.conn, out["task_id"]).origin == TELEGRAM


def test_revoking_stops_the_sender_at_once(ctx, api):
    allow(api)
    assert api.delete(f"/channels/telegram/allow/{NEW}").status_code == 200
    assert TelegramBot(ctx, FakeTransport()).handle_update(msg(NEW))["action"] == "dropped"


def test_a_configured_sender_cannot_be_revoked_here(api):
    r = api.delete(f"/channels/telegram/allow/{CONFIGURED}")
    assert r.status_code == 409 and "config" in r.json()["detail"]
    assert api.delete("/channels/telegram/allow/555").status_code == 404


def test_allowing_twice_is_one_entry(api):
    allow(api)
    allow(api)
    assert [a["sender"] for a in api.get("/panels/channels").json()["allowed"]].count(NEW) == 1


def test_a_dropped_sender_is_offered_for_allowing(ctx, api):
    record_drop(ctx.conn, TELEGRAM, NEW, "sender not allowlisted", "hello")
    assert api.get("/panels/channels").json()["drops"][0]["sender"] == NEW


def test_the_page_offers_allow_and_revoke_through_its_routes_only():
    assert {("POST", "/channels/telegram/allow"), ("DELETE", "/channels/telegram/allow/{}")} <= _calls(UI)
    assert "allowSender" in UI and "revokeSender" in UI and "allowSender" in UI + HTML
