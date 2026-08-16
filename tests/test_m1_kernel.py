"""M1 acceptance — chain, idempotency, and a real kill-and-resume."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

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
    proc.kill()
    proc.wait(timeout=10)

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
