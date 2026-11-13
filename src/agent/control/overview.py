from __future__ import annotations

import json

from ..security.origin import BENCH

CUT = 200


def _cut(d: dict) -> dict:
    return {k: (v[:CUT] if isinstance(v, str) else v) for k, v in d.items()}


def overview(ctx, events: tuple[str, ...], limit: int) -> dict:
    rows = ctx.conn.execute(
        "SELECT t.id, t.parent_id, t.contract_json, t.status, t.created_at, ct.conversation_id, ct.position "
        "FROM tasks t LEFT JOIN conversation_tasks ct ON ct.task_id = t.id "
        "WHERE t.origin != ? ORDER BY t.created_at DESC, t.rowid DESC LIMIT ?", (BENCH, limit)).fetchall()
    rows = rows[::-1]
    marks = ",".join("?" * len(events))
    tasks = []
    for tid, parent, contract, status, created, cid, _pos in rows:
        evs = ctx.conn.execute(f"SELECT type, payload_json FROM events WHERE task_id=? AND type IN ({marks}) ORDER BY seq",
                               (tid, *events)).fetchall()
        goal = (json.loads(contract).get("goal") or "").strip().splitlines()
        tasks.append({"id": tid, "parent_id": parent, "goal": (goal[0] if goal else "")[:CUT], "status": status,
                      "created_at": created, "conversation_id": cid,
                      "events": [{"type": t, "data": _cut(json.loads(p))} for t, p in evs]})
    ids = [r[5] for r in rows if r[5]]
    convs = []
    for cid in dict.fromkeys(ids):
        title = ctx.conn.execute("SELECT title FROM conversations WHERE id=?", (cid,)).fetchone()
        order = sorted((r[6], r[0]) for r in rows if r[5] == cid)
        convs.append({"id": cid, "title": title[0] if title else "", "tasks": [t for _, t in order]})
    return {"conversations": convs, "tasks": tasks}
