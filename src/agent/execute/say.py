from __future__ import annotations

import json

from ..kernel.tasks import load_task
from ..kernel.types import Taint, TaskStatus
from ..ledger.entries import LedgerEntry

MAX_LEN = 4000
OPEN = (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED)


class SayError(RuntimeError):
    pass


def say(ctx, task_id: str, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise SayError("empty message")
    if len(text) > MAX_LEN:
        raise SayError(f"too long ({len(text)} > {MAX_LEN} characters)")
    if load_task(ctx.conn, task_id).status not in OPEN:
        raise SayError("the task has finished; ask again or steer it")
    ctx.log.append(task_id, "human.message", {"text": text})
    return {"task_id": task_id}


def take(ctx, task, step: int) -> list[LedgerEntry]:
    done = ctx.conn.execute(
        "SELECT COALESCE(MAX(json_extract(payload_json, '$.upto')), -1) FROM events "
        "WHERE task_id=? AND type='human.message_delivered'", (task.id,)).fetchone()[0]
    rows = ctx.conn.execute(
        "SELECT seq, payload_json FROM events WHERE task_id=? AND type='human.message' AND seq>? ORDER BY seq",
        (task.id, done)).fetchall()
    if not rows:
        return []
    ctx.log.append(task.id, "human.message_delivered", {"upto": rows[-1][0], "count": len(rows), "step": step})
    tracker = ctx.taint_tracker(task.id)
    out = []
    for _, payload in rows:
        text = json.loads(payload)["text"]
        tracker.observe(text, Taint.USER)
        e = LedgerEntry.observation("The user says, while you work: " + text, taint=Taint.USER, label="user-message")
        e.pinned = True
        out.append(e)
    return out
