"""AUDIT-2026-09 evidence: a channel callback decides approvals that are not
the channel's (DR1, width). Uses FakeTransport; no network.

An approval created by a LOCAL task (never sent to any phone; resend_pending
skips it) is decided by a Telegram callback naming its id. The allowlist is
checked; whether the approval belongs to a task bound to this channel is not.
Green = demonstration.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))

from test_channel import callback, make_cfg  # noqa: E402

from agent.channel.telegram import FakeTransport, TelegramBot  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402


def test_F_channel_callback_decides_a_local_tasks_approval(tmp_path):
    ctx = AgentContext(make_cfg(tmp_path))
    local = create_task(ctx.conn, ctx.log,
                        TaskContract(goal="local work", artifact_scope=["work"]),
                        Budget(usd=0, seconds=60))            # origin: local
    aid = ctx.request_approval(local, ctx.tools.get("write_file"),
                               {"path": "work/x", "content": "y"}, "needs a human", 1)
    bot = TelegramBot(ctx, FakeTransport())
    bot.run = lambda task_id: {"action": "not run in this probe"}
    assert bot.resend_pending() == []          # correctly never ASKED the phone...
    out = bot.handle_update(callback(aid))     # ...but a phone can still DECIDE it
    print(out)
    assert out["action"] == "approve" and out["task_id"] == local.id
    st = ctx.conn.execute("SELECT status FROM approvals WHERE id=?", (aid,)).fetchone()[0]
    assert st == "approved"
    ctx.close()
