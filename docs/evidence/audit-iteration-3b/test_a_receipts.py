"""D27 - claim-then-execute receipts (src/agent/kernel/receipts.py).

The fill-in step after a successful effect is outside every handler: if it
raises, the Event is never set and the _inflight entry is never popped.
The trigger used here is a second writer holding SQLite's write lock while
the effect runs (the documented "CLI run against the same database while the
server is up"); busy_timeout is lowered from 30 s to 0.5 s only so the probe
is quick - the production path is identical after 30 s.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent.kernel import receipts
from agent.kernel.receipts import _CLAIM, execute_once, idempotency_key

ARGS = {"to": "a@b.c", "body": "hi"}


def _row(conn, key):
    r = conn.execute("SELECT result_json FROM receipts WHERE idempotency_key=?", (key,)).fetchone()
    return r[0] if r else None


def test_FINDING_a_failed_fill_in_leaves_a_stale_inflight_entry_and_the_same_key_hangs(ctx, task, tmp_path):
    key = idempotency_key(task.id, 3, "send_message", ARGS)
    marker = tmp_path / "sent.txt"
    other = sqlite3.connect(ctx.config.db_path, timeout=0.1, isolation_level=None, check_same_thread=False)
    ctx.conn.execute("PRAGMA busy_timeout = 500")

    def effect():
        with marker.open("a") as f:
            f.write("sent\n")                    # the effect HAPPENED
        other.execute("BEGIN IMMEDIATE")         # another writer holds the lock now
        return {"ok": True, "id": 1}

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        execute_once(ctx.conn, key, "send_message", ARGS, effect)
    other.execute("ROLLBACK")                    # the other writer finishes
    other.close()

    assert marker.read_text().count("\n") == 1
    assert _row(ctx.conn, key) == _CLAIM, "the receipt was never filled in"
    ev = receipts._inflight.get(key)
    assert ev is not None and not ev.is_set(), "stale in-flight entry, Event never set"

    # The same key again in THIS process (a replay / resume in the server
    # process): it waits on an Event nobody will ever set.
    out = {}
    t = threading.Thread(target=lambda: out.setdefault(
        "r", execute_once(ctx.conn, key, "send_message", ARGS, effect)), daemon=True)
    t.start()
    t.join(5)
    hung = t.is_alive()
    print(f"\nrow={_row(ctx.conn, key)} inflight={key in receipts._inflight} "
          f"second call still blocked after 5 s: {hung}")
    # cleanup: pop the stale entry, then wake the waiter (it returns outcome_unknown)
    receipts._inflight.pop(key, None).set()
    t.join(5)
    assert hung, "a second call on the same key did not hang"
    assert out["r"].get("outcome_unknown") is True
    assert marker.read_text().count("\n") == 1


def test_FINDING_the_loop_reports_a_performed_effect_as_failed_when_the_fill_in_fails(ctx, task, tmp_path):
    """loop._perform catches the fill-in's exception as a tool failure: the model
    is told ok=False for a write that happened (pre-D27 this was the same shape;
    D27 adds the stale claim and in-flight entry above)."""
    from agent.execute import loop

    spec = ctx.tools.get("write_file")
    assert spec.side_effecting
    other = sqlite3.connect(ctx.config.db_path, timeout=0.1, isolation_level=None, check_same_thread=False)
    ctx.conn.execute("PRAGMA busy_timeout = 500")
    real = spec.fn

    def fn(c, t, **a):
        r = real(c, t, **a)
        other.execute("BEGIN IMMEDIATE")           # held past the fill-in's 0.5 s wait,
        threading.Timer(0.8, lambda: other.execute("ROLLBACK")).start()   # released before the receipt append gives up
        return r

    object.__setattr__(spec, "fn", fn) if hasattr(spec, "__dataclass_fields__") else setattr(spec, "fn", fn)
    try:
        entry = loop._perform(ctx, task, spec, "write_file",
                              {"path": "work/out.txt", "content": "written"}, 7)
    finally:
        time.sleep(1.0)
        other.close()
        object.__setattr__(spec, "fn", real) if hasattr(spec, "__dataclass_fields__") else setattr(spec, "fn", real)
        for k in [k for k in receipts._inflight]:
            receipts._inflight.pop(k).set()
    written = (Path(ctx.config.root) / "work" / "out.txt").read_text()
    receipt = ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='tool.receipt' ORDER BY seq DESC LIMIT 1",
        (task.id,)).fetchone()
    print(f"\nfile content: {written!r}\nobservation: {entry.content[:160]!r}\nreceipt: {receipt[0]}")
    assert written == "written"
    assert json.loads(receipt[0])["ok"] is False


def test_clean_two_threads_one_key_run_the_effect_once(ctx, task):
    key = idempotency_key(task.id, 1, "send_message", ARGS)
    calls, started, release = [], threading.Event(), threading.Event()

    def slow():
        calls.append(1)
        started.set()
        release.wait(10)
        return {"ok": True}

    res = []
    a = threading.Thread(target=lambda: res.append(execute_once(ctx.conn, key, "send_message", ARGS, slow)))
    a.start()
    assert started.wait(5)
    b = threading.Thread(target=lambda: res.append(execute_once(ctx.conn, key, "send_message", ARGS, slow)))
    b.start()
    time.sleep(0.3)
    assert b.is_alive(), "second thread should wait"
    release.set()
    a.join(5), b.join(5)
    assert len(calls) == 1 and res == [{"ok": True}, {"ok": True}]
    assert key not in receipts._inflight


def test_clean_a_cut_off_effect_leaves_the_claim_and_a_waiter_gets_outcome_unknown(ctx, task):
    key = idempotency_key(task.id, 2, "send_message", ARGS)
    started, go = threading.Event(), threading.Event()

    def cut():
        started.set()
        go.wait(5)
        raise KeyboardInterrupt

    errs, res = [], []

    def owner():
        try:
            execute_once(ctx.conn, key, "send_message", ARGS, cut)
        except KeyboardInterrupt:
            errs.append("cut")

    a = threading.Thread(target=owner)
    a.start()
    assert started.wait(5)
    b = threading.Thread(target=lambda: res.append(
        execute_once(ctx.conn, key, "send_message", ARGS, lambda: {"ok": True})))
    b.start()
    time.sleep(0.2)
    go.set()
    a.join(5), b.join(5)
    assert errs == ["cut"] and res[0].get("outcome_unknown") is True
    assert _row(ctx.conn, key) == _CLAIM and key not in receipts._inflight


def test_note_a_waiter_reruns_an_effect_whose_owner_raised(ctx, task):
    """Not a regression (the old lock-across-effect code did the same): when the
    owner's effect raises, a thread already waiting on that key runs it again
    without its caller deciding to retry. Recorded because the docstring's
    'two threads with the same key run it once' reads broader than this."""
    key = idempotency_key(task.id, 4, "send_message", ARGS)
    calls, started, go = [], threading.Event(), threading.Event()

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            started.set()
            go.wait(5)
            raise ConnectionError("half-sent")
        return {"ok": True}

    a = threading.Thread(target=lambda: pytest.raises(ConnectionError, execute_once,
                                                      ctx.conn, key, "send_message", ARGS, flaky))
    a.start()
    assert started.wait(5)
    res = []
    b = threading.Thread(target=lambda: res.append(execute_once(ctx.conn, key, "send_message", ARGS, flaky)))
    b.start()
    time.sleep(0.2)
    go.set()
    a.join(5), b.join(5)
    print(f"\neffect calls: {len(calls)}")
    assert len(calls) == 2


def test_FINDING_an_approved_action_with_an_unknown_outcome_is_recorded_as_executed(ctx, task, tmp_path):
    """_release_decided (loop.py) writes approval.consumed outcome="executed"
    for whatever _perform returned - including D27's outcome_unknown, where the
    action was NOT executed by this run and may never have happened."""
    from agent.execute import loop

    args = {"path": "work/approved.txt", "content": "x"}
    spec = ctx.tools.get("write_file")
    step = 5
    key = idempotency_key(task.id, step, "write_file", args)
    # The state an earlier process leaves when it died inside the effect.
    ctx.conn.execute("INSERT INTO receipts(idempotency_key, tool_name, args_hash, result_json, created_at)"
                     " VALUES (?,?,?,?,?)", (key, "write_file", "h", _CLAIM, time.time()))
    aid = ctx.request_approval(task, spec, args, "needs a human", step)
    ctx.resolve_approval(aid, True)
    entries = loop._release_decided(ctx, task)
    consumed = ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='approval.consumed'", (task.id,)).fetchone()
    print(f"\nobservation: {entries[0].content[:120]!r}\napproval.consumed: {consumed[0]}")
    assert "outcome_unknown" in entries[0].content
    assert json.loads(consumed[0])["outcome"] == "executed"
    assert not (Path(ctx.config.root) / "work" / "approved.txt").exists()
