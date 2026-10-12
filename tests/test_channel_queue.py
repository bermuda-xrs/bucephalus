from __future__ import annotations

import threading
import time

import pytest
from test_channel import callback, channel_task, make_cfg, message

from agent.channel.telegram import FakeTransport, TelegramBot
from agent.context import AgentContext
from agent.kernel.tasks import load_task


@pytest.fixture
def bot(tmp_path):
    ctx = AgentContext(make_cfg(tmp_path))
    b = TelegramBot(ctx, FakeTransport())
    yield b
    ctx.close()


def _texts(bot):
    return [m["text"] for m in bot.tx.sent]


def test_the_first_message_starts_and_the_next_ones_say_how_many_are_ahead(bot):
    bot.handle_update(message(text="one", uid=1))
    bot.handle_update(message(text="two", uid=2))
    bot.handle_update(message(text="three", uid=3))
    t = _texts(bot)
    assert t[0].startswith("started ") and "ahead" not in t[0]
    assert t[1].startswith("queued ") and "1 ahead" in t[1]
    assert t[2].startswith("queued ") and "2 ahead" in t[2]


def test_the_queue_runs_oldest_first_one_at_a_time(bot):
    for i, goal in enumerate(("first", "second", "third"), 1):
        bot.handle_update(message(text=goal, uid=i))
    order = []
    bot.ctx.backends["cloud"].responder = None
    real = bot.run

    def run(task_id):
        order.append(load_task(bot.ctx.conn, task_id).contract.goal)
        return real(task_id)

    bot.run = run
    while bot.drain_once():
        pass
    assert order == ["first", "second", "third"]
    done = [t for t in _texts(bot) if t.split(chr(10))[0].endswith(" done")]
    assert len(done) == 3 and bot.drain_once() is None


def test_a_full_queue_refuses_and_creates_nothing(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.channels.telegram.max_queue = 2
    ctx = AgentContext(cfg)
    b = TelegramBot(ctx, FakeTransport())
    try:
        assert b.handle_update(message(uid=1))["action"] == "created"
        assert b.handle_update(message(uid=2))["action"] == "created"
        out = b.handle_update(message(uid=3))
        assert out["action"] == "refused" and "queue is full" in b.tx.sent[-1]["text"]
        assert ctx.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
    finally:
        ctx.close()


def test_a_new_bot_picks_up_what_the_old_one_left_waiting(tmp_path):
    ctx = AgentContext(make_cfg(tmp_path))
    first = TelegramBot(ctx, FakeTransport())
    first.handle_update(message(text="left behind", uid=1))
    again = TelegramBot(ctx, FakeTransport())
    assert again.drain_once() is not None
    assert ctx.conn.execute("SELECT status FROM tasks").fetchone()[0] == "done"
    assert any(m["text"].split(chr(10))[0].endswith(" done") for m in again.tx.sent)
    ctx.close()


def test_a_message_that_arrives_during_a_run_is_acknowledged_at_once(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.channels.telegram.poll_timeout_s = 0
    ctx = AgentContext(cfg)
    gate, inside = threading.Event(), threading.Event()
    mock = ctx.backends["cloud"]

    def responder(prefix, tail):
        inside.set()
        gate.wait(20)
        return '<final>{"answer": "ok"}</final>'

    mock._responder = responder
    tx = FakeTransport()
    b = TelegramBot(ctx, tx)
    stop = threading.Event()
    t = threading.Thread(target=b.work_queue, args=(stop, 0.05), daemon=True)
    try:
        tx.updates.append(message(text="long job", uid=1))
        t.start()
        assert inside.wait(15), "the first task never started"
        tx.updates.append(message(text="another", uid=2))
        deadline = time.time() + 10
        while time.time() < deadline and not any(m["text"].startswith("queued ") for m in tx.sent):
            time.sleep(0.05)
        queued = [m["text"] for m in tx.sent if m["text"].startswith("queued ")]
        assert queued and "1 ahead" in queued[0], tx.sent
        assert not any(" done" in m["text"].split(chr(10))[0] for m in tx.sent), "acknowledged while the first was still running"
    finally:
        gate.set()
        stop.set()
        t.join(30)
        ctx.close()
    assert not t.is_alive()


def test_an_approval_in_serve_mode_is_resumed_by_the_worker_not_the_poll(bot, monkeypatch):
    task = channel_task(bot.ctx)
    bot._bind(task.id, 7000)
    appr = bot.ctx.request_approval(task, bot.ctx.tools.get("write_file"), {"path": "work/x", "content": "y"}, "needs you", 0)
    bot.queued = True
    monkeypatch.setattr(bot, "run", lambda *_: pytest.fail("the poll thread must not run the task"))
    out = bot.handle_update(callback(appr))
    assert out["action"] == "approve" and list(bot._resume) == [task.id]


def test_without_serve_mode_an_approval_still_runs_inline(bot, monkeypatch):
    task = channel_task(bot.ctx)
    bot._bind(task.id, 7000)
    appr = bot.ctx.request_approval(task, bot.ctx.tools.get("write_file"), {"path": "work/x", "content": "y"}, "needs you", 0)
    ran = []
    monkeypatch.setattr(bot, "run", lambda tid: ran.append(tid))
    bot.handle_update(callback(appr))
    assert ran == [task.id]


HTTP_TRANSPORT_SHA256 = "3114c79fd77faa437206e8a9abecf0b3e316cb500f3b9e7bdfb5937b1d80fc0b"


def test_the_http_transport_is_untouched():
    import hashlib
    import inspect

    from agent.channel import telegram

    got = hashlib.sha256(inspect.getsource(telegram.HttpTransport).encode()).hexdigest()
    assert got == HTTP_TRANSPORT_SHA256, "HttpTransport has never made a live call (D26) and must not change without one"
