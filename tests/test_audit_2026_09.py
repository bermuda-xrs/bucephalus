"""The two HIGH findings of the independent audit (docs/AUDIT-2026-09.md).

F1 - a Telegram callback decided approvals that were not the channel's. The
     allowlist guarded WHO may press a button, not WHICH decisions this
     channel owns: an allowlisted - or compromised - sender could approve a
     LOCAL task's parked action that was never sent to any phone.
F2 - GET / handed the console's bearer token to any local caller. The guard's
     rationale covered a cross-origin page only; another local process - and
     sandboxed code, which can open a loopback socket (F3) - read the token
     and could then drive every authenticated route, approvals included.
"""

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


# ------------------------------------------------------------------ F1

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
    # ...and the chat it was sent to still decides it.
    assert bot.handle_update(callback(aid, uid=3))["action"] == "approve"
    assert _status(ctx, aid) == "approved"
    ctx.close()


# ------------------------------------------------------------------ F2

def test_F2_the_console_page_does_not_carry_the_token(ctx, workspace):
    from agent import desktop

    server, thread, port = desktop.start_server(ctx, "127.0.0.1")
    try:
        token = server.config.app.state.token
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            body = r.read().decode("utf-8")
        assert r.status == 200 and "alpha" in body
        assert token not in body, "any local caller reads the bearer token from GET /"
    finally:
        desktop.shutdown(server, thread)


def test_F2_the_token_reaches_the_console_in_the_url_fragment():
    """A fragment is never sent to the server, so it is in no request log;
    the launcher that owns the token puts it there, and the page reads it."""
    from agent.control.server import console_url

    url = console_url(4321, "tok-123")
    assert url == "http://127.0.0.1:4321/#token=tok-123"
    ui = (Path(__file__).resolve().parents[1] / "src" / "agent" / "control" / "ui.html"
          ).read_text(encoding="utf-8")
    assert "__ALPHA_TOKEN__" not in ui
    assert "location.hash" in ui
