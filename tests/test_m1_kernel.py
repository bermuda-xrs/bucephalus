"""M1 acceptance — chain, idempotency, and a real kill-and-resume."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from agent.proc import kill_tree
import pytest

from agent.kernel.events import canonical
from agent.kernel.integrity import IntegrityError, checkpoint, resume
from agent.kernel.receipts import execute_once, idempotency_key


def test_chain_detects_tampering(ctx, task):
    log = ctx.log
    for i in range(5):
        log.append(task.id, "task.status_changed", {"i": i})
    assert log.verify(task.id) == (True, None)

    ctx.conn.execute(
        "UPDATE events SET payload_json=? WHERE task_id=? AND seq=2",
        ('{"i":999}', task.id),
    )
    ctx.conn.commit()
    ok, bad = log.verify(task.id)
    assert ok is False and bad == 2


def test_chain_detects_deletion(ctx, task):
    for i in range(4):
        ctx.log.append(task.id, "task.status_changed", {"i": i})
    ctx.conn.execute("DELETE FROM events WHERE task_id=? AND seq=2", (task.id,))
    ctx.conn.commit()
    ok, bad = ctx.log.verify(task.id)
    assert ok is False


def test_idempotency_survives_replay(ctx, task):
    calls: list[int] = []
    key = idempotency_key("t1", 0, "send", {"to": "a@b.c"})
    for _ in range(3):
        execute_once(
            ctx.conn, key, "send", {"to": "a@b.c"},
            lambda: (calls.append(1), {"sent": True})[1],
        )
    assert len(calls) == 1


def test_anchor_mismatch_halts(ctx, task):
    ctx.log.append(task.id, "task.status_changed", {"a": 1})
    checkpoint(ctx.log, ctx.anchor, task.id)
    # Someone rewrites the whole log and recomputes every hash: internally
    # consistent, but the anchor remembers a head that no longer exists.
    ctx.conn.execute("DELETE FROM events WHERE task_id=?", (task.id,))
    ctx.conn.commit()
    ctx.log.append(task.id, "task.created", {"rewritten": True})
    with pytest.raises(IntegrityError):
        resume(ctx.conn, task.id, ctx.log, ctx.anchor)


def test_canonical_is_order_independent():
    a = canonical({"b": 1, "a": [1, 2], "c": {"y": 2, "x": 1}})
    b = canonical({"c": {"x": 1, "y": 2}, "a": [1, 2], "b": 1})
    assert a == b


def test_unknown_event_type_is_rejected(ctx, task):
    with pytest.raises(ValueError, match="unknown event type"):
        ctx.log.append(task.id, "monitor.finding", {"leak": True})


def test_chain_survives_concurrent_appends(ctx, task):
    """The control plane serves on a threadpool, so appends race.

    Without a lock around the read-head/write-successor section, two threads
    read the same prev_hash and fork the chain — which verify() then reports
    as tampering, on a database nobody tampered with.
    """
    import threading

    errors: list[str] = []

    def hammer(n: int) -> None:
        try:
            for i in range(10):
                ctx.log.append(task.id, "task.status_changed", {"t": n, "i": i})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    ok, bad = ctx.log.verify(task.id)
    assert ok, f"concurrent appends forked the chain at seq {bad}"

    seqs = [
        r[0] for r in ctx.conn.execute(
            "SELECT seq FROM events WHERE task_id=? ORDER BY seq", (task.id,)
        ).fetchall()
    ]
    assert seqs == list(range(len(seqs))), "sequence numbers are not contiguous"


def test_execute_once_is_atomic_under_threads(ctx, task):
    """Two threads hitting the same idempotency key must not both send."""
    import threading

    performed: list[int] = []
    key = idempotency_key(task.id, 0, "send_message", {"to": "a@b.c"})

    def send() -> None:
        execute_once(
            ctx.conn, key, "send_message", {"to": "a@b.c"},
            lambda: (performed.append(1), {"ok": True})[1],
        )

    threads = [threading.Thread(target=send) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(performed) == 1, f"the side effect ran {len(performed)} times"


def test_db_connection_is_usable_across_threads(ctx):
    """M12 runs on a threadpool; a thread-bound connection 500s every endpoint."""
    import threading

    err: dict[str, str] = {}

    def query() -> None:
        try:
            ctx.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
        except Exception as e:  # noqa: BLE001
            err["e"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=query)
    t.start()
    t.join()
    assert not err, err


def test_kill_and_resume(tmp_path: Path):
    """Start a task, SIGKILL mid-run, resume, verify no duplicate side effects.

    A real kill (TerminateProcess / SIGKILL), not a mocked exception — the
    whole point is that no `finally` block gets to run.
    """
    child = Path(__file__).parent / "_crash_child.py"
    marker = tmp_path / "marker.txt"
    db = tmp_path / "crash.db"

    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, str(child), str(db), str(marker), "hang"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Wait for the side effect to land, then kill without warning.
    deadline = time.time() + 30
    while time.time() < deadline and not marker.exists():
        time.sleep(0.05)
    assert marker.exists(), "child never performed the side effect"
    time.sleep(0.2)

    # Kill the REAL interpreter, not the venv trampoline. On Windows a venv's
    # python.exe is a wrapper: Popen returns its pid while the interpreter
    # runs as a child. proc.kill() alone therefore left the worker alive, and
    # this test's claim of "a real kill, no finally block runs" was false -
    # it passed on the marker file regardless. Found via test_runlock.
    kill_tree(proc.pid)
    proc.kill()
    proc.wait(timeout=10)
    proc.stdout.close(), proc.stderr.close()

    assert marker.read_text(encoding="utf-8").count("\n") == 1

    # Resume: same task, same step, same args -> the receipt short-circuits it.
    out = subprocess.run(  # noqa: S603
        [sys.executable, str(child), str(db), str(marker), "resume"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["replayed"] is True
    assert marker.read_text(encoding="utf-8").count("\n") == 1, (
        "the side effect ran twice across a crash"
    )


# D27: claim-then-execute. The receipt is CLAIMED before the side effect runs
# and filled in after, and the chain lock is not held in between.
def test_a_run_that_died_mid_effect_does_not_repeat_it(tmp_path: Path):
    """Killed inside the effect - after it happened, before its receipt - a
    replay repeated it: a second send. Now the claim says it was started, and
    the replay reports the outcome as unknown instead of sending again."""
    child = Path(__file__).parent / "_crash_child.py"
    marker, db = tmp_path / "marker.txt", tmp_path / "crash.db"
    first = subprocess.run([sys.executable, str(child), str(db), str(marker), "die-mid-effect"],
                           capture_output=True, text=True, timeout=60)
    assert first.returncode == 3, first.stderr
    assert marker.read_text(encoding="utf-8").count("\n") == 1
    out = subprocess.run([sys.executable, str(child), str(db), str(marker), "resume"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert marker.read_text(encoding="utf-8").count("\n") == 1, "the effect ran twice across a crash"
    assert payload["replayed"] is True and payload["result"].get("outcome_unknown") is True, payload


def test_a_slow_effect_does_not_hold_the_chain(ctx, task):
    """The chain lock was held for the whole side effect, so a slow one - a
    push, a send - froze every event append in the process: the console's
    approvals, other tasks' steps."""
    import threading

    started, release = threading.Event(), threading.Event()
    key = idempotency_key(task.id, 0, "git_push", {"remote": "origin"})

    def slow() -> dict:
        started.set()
        release.wait(30)
        return {"ok": True}

    effect = threading.Thread(target=execute_once,
                              args=(ctx.conn, key, "git_push", {"remote": "origin"}, slow))
    effect.start()
    try:
        assert started.wait(10)
        appended = threading.Event()
        threading.Thread(target=lambda: (ctx.log.append(task.id, "human.steered",
                                                        {"correction": "x"}), appended.set())).start()
        assert appended.wait(5), "an event append waited for another task's side effect"
    finally:
        release.set()
        effect.join(30)


def test_a_failed_effect_can_be_retried(ctx, task):
    """An effect that raised left no receipt and could be tried again; the
    claim must not change that."""
    key = idempotency_key(task.id, 0, "send_message", {"to": "a@b.c"})
    calls = []

    def flaky() -> dict:
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("the network blinked")
        return {"ok": True}

    with pytest.raises(ConnectionError):
        execute_once(ctx.conn, key, "send_message", {"to": "a@b.c"}, flaky)
    assert execute_once(ctx.conn, key, "send_message", {"to": "a@b.c"}, flaky) == {"ok": True}
    assert len(calls) == 2


# AUDIT-ITERATION-3B, M2: the fill-in after the effect was not exception-safe.
# A commit that failed (another writer past busy_timeout) left the claim, a
# stale in-flight Event nothing would ever set, and a same-key retry that
# waited on it forever - while the effect HAD happened.
class _FillInFails:
    """A connection whose post-effect UPDATE of the receipt fails."""

    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, *a):
        if sql.lstrip().upper().startswith("UPDATE RECEIPTS"):
            raise sqlite3.OperationalError("database is locked")
        return self._c.execute(sql, *a)

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_3B_M2_a_failed_fill_in_keeps_the_result_and_releases_the_waiters(ctx, task):
    import threading

    from agent.kernel import receipts

    key = idempotency_key(task.id, 0, "send_message", {"to": "a@b.c"})
    ran = []
    flaky = _FillInFails(ctx.conn)
    result = execute_once(flaky, key, "send_message", {"to": "a@b.c"},
                          lambda: (ran.append(1), {"ok": True})[1])
    assert result == {"ok": True}, "the effect happened; the caller is told so"
    assert key not in receipts._inflight, "a stale in-flight entry outlived the call"
    # The row still says CLAIMED - honest: its outcome was never recorded - and
    # a replay must answer at once, not wait on an Event nothing will set.
    out = []
    t = threading.Thread(target=lambda: out.append(
        execute_once(ctx.conn, key, "send_message", {"to": "a@b.c"}, lambda: ran.append(2))))
    t.start()
    t.join(10)
    assert not t.is_alive(), "a same-key retry hung on a stranded claim"
    assert out and out[0].get("outcome_unknown") is True and ran == [1], (out, ran)


def test_3B_M2_a_waiter_gives_up_on_an_owner_that_never_finishes(ctx, task, monkeypatch):
    import threading

    from agent.kernel import receipts

    key = idempotency_key(task.id, 0, "send_message", {"to": "z@b.c"})
    ctx.conn.execute("INSERT INTO receipts(idempotency_key, tool_name, args_hash, result_json, "
                     "created_at) VALUES (?,?,?,?,?)", (key, "send_message", "h", receipts._CLAIM, 0.0))
    ctx.conn.commit()
    monkeypatch.setitem(receipts._inflight, key, threading.Event())     # never set
    monkeypatch.setattr(receipts, "WAIT_S", 0.5)
    out = []
    t = threading.Thread(target=lambda: out.append(
        execute_once(ctx.conn, key, "send_message", {"to": "z@b.c"}, lambda: {"ok": True})))
    t.start()
    t.join(10)
    assert not t.is_alive() and out and out[0].get("outcome_unknown") is True, out
