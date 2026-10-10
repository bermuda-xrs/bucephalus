from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_channel import callback, channel_task, make_cfg  # noqa: E402

from agent.channel.telegram import FakeTransport, TelegramBot  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402


def _approval(ctx, task):
    return ctx.request_approval(task, ctx.tools.get("write_file"),
                                {"path": "work/x", "content": "y"}, "needs a human", 1)


def _status(ctx, aid):
    return ctx.conn.execute("SELECT status FROM approvals WHERE id=?", (aid,)).fetchone()[0]


def _bot(ctx):
    bot = TelegramBot(ctx, FakeTransport())
    bot.run = lambda task_id: {"action": "not run here"}
    return bot


def test_F1_a_channel_cannot_decide_a_local_tasks_approval(tmp_path):
    ctx = AgentContext(make_cfg(tmp_path))
    local = create_task(ctx.conn, ctx.log,
                        TaskContract(goal="local work", artifact_scope=["work"]),
                        Budget(usd=0, seconds=60))
    aid = _approval(ctx, local)
    out = _bot(ctx).handle_update(callback(aid))
    assert out["action"] == "refused", out
    assert _status(ctx, aid) == "pending", "a phone decided a local task's approval"
    ctx.close()


def test_F1_a_channel_task_is_decided_only_from_its_own_chat(tmp_path):
    ctx = AgentContext(make_cfg(tmp_path))
    bot = _bot(ctx)
    t = channel_task(ctx)
    ctx.conn.execute("INSERT INTO channel_routes(task_id, channel, chat_id, created_at) "
                     "VALUES (?,?,?,?)", (t.id, "telegram", "7000", time.time()))
    ctx.conn.commit()
    aid = _approval(ctx, t)
    other = callback(aid)
    other["callback_query"]["message"]["chat"]["id"] = 9999
    assert bot.handle_update(other)["action"] == "refused"
    assert _status(ctx, aid) == "pending"
    assert bot.handle_update(callback(aid, uid=3))["action"] == "approve"
    assert _status(ctx, aid) == "approved"
    ctx.close()


def test_F2_the_console_page_does_not_carry_the_token(ctx, workspace):
    from agent import desktop

    server, thread, port = desktop.start_server(ctx, "127.0.0.1")
    try:
        token = server.config.app.state.token
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            body = r.read().decode("utf-8")
        assert r.status == 200 and "Bucephalus" in body
        assert token not in body, "any local caller reads the bearer token from GET /"
    finally:
        desktop.shutdown(server, thread)


def test_F2_the_token_reaches_the_console_in_the_url_fragment():
    from agent.control.server import console_url

    url = console_url(4321, "tok-123")
    assert url == "http://127.0.0.1:4321/#token=tok-123"
    ctl = Path(__file__).resolve().parents[1] / "src" / "agent" / "control"
    ui = "".join((ctl / n).read_text(encoding="utf-8") for n in ("ui.html", "ui.js"))
    assert "__BUCEPHALUS_TOKEN__" not in ui
    assert "location.hash" in ui
