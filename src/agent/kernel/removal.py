from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from .events import GENESIS
from .integrity import ANCHOR_REWRITTEN, REVOKED, TOMBSTONE, _classify

LIVE = ("running", "paused")
TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
DELETE = ("artifacts", "approvals", "plans", "sessions", "channel_routes", "conversation_tasks",
          "review_decisions", "monitor_flags", "deviations")
DETACH = ("schedule_firings", "notifications", "webhook_deliveries")


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def with_descendants(conn, ids: list[str]) -> list[str]:
    out = list(dict.fromkeys(ids))
    frontier = out
    while frontier:
        marks = ",".join("?" * len(frontier))
        kids = [r[0] for r in conn.execute(f"SELECT id FROM tasks WHERE parent_id IN ({marks})", frontier)]
        frontier = [k for k in kids if k not in out]
        out += frontier
    return out


def remove_tasks(ctx, ids: list[str]) -> dict:
    conn, log = ctx.conn, ctx.log
    ids = with_descendants(conn, ids)
    marks = ",".join("?" * len(ids))
    rows = dict(conn.execute(f"SELECT id, status FROM tasks WHERE id IN ({marks})", ids).fetchall())
    if len(rows) != len(ids) or not all(TASK_ID.match(i) for i in ids):
        raise Refused(404, "no such task")
    live = [i for i in ids if rows[i] in LIVE]
    if live:
        raise Refused(409, f"{len(live)} task(s) are running or paused; stop them first")
    heads = ctx.anchor.chain_heads()
    for t in ids:
        ok, bad = log.verify(t)
        if not ok or _classify(conn, log, t, heads.get(t)) == ANCHOR_REWRITTEN:
            raise Refused(409, f"{t}'s record already fails verification; deleting it would destroy the evidence")
    keys = [json.loads(p).get("key") for (p,) in conn.execute(
        f"SELECT payload_json FROM events WHERE type='tool.intent' AND task_id IN ({marks})", ids)]
    counts = dict(conn.execute(f"SELECT task_id, COUNT(*) FROM events WHERE task_id IN ({marks}) GROUP BY task_id", ids))
    now, last = time.time(), {t: log.head(t) or GENESIS for t in ids}
    marked, committed = [], False
    conn.execute("PRAGMA secure_delete = ON")
    try:
        for t in ids:
            ctx.anchor.record(TOMBSTONE + t, last[t])
            marked.append(t)
        with conn:
            conn.execute(f"DELETE FROM events WHERE task_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM search_index WHERE task_id IN ({marks})", ids)
            for table in DELETE:
                conn.execute(f"DELETE FROM {table} WHERE task_id IN ({marks})", ids)
            for table in DETACH:
                conn.execute(f"UPDATE {table} SET task_id = NULL WHERE task_id IN ({marks})", ids)
            for k in filter(None, keys):
                conn.execute("DELETE FROM receipts WHERE idempotency_key = ?", (k,))
            for t in ids:
                conn.execute("INSERT OR REPLACE INTO task_tombstones(task_id, removed_at, head, events) VALUES (?,?,?,?)",
                             (t, now, last[t], counts.get(t, 0)))
            conn.execute(f"DELETE FROM tasks WHERE id IN ({marks})", ids)
        committed = True
        conn.execute("INSERT INTO search_index(search_index) VALUES ('optimize')")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except BaseException:
        if not committed:
            for t in marked:
                ctx.anchor.record(TOMBSTONE + t, REVOKED)
        raise
    finally:
        conn.execute("PRAGMA secure_delete = OFF")
    shadow = Path(ctx.config.resolve_state("shadow")).resolve()
    for t in ids:
        d = (shadow / t).resolve()
        if d.parent == shadow and d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    return {"removed": ids, "records": sum(counts.values())}
