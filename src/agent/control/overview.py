from __future__ import annotations

import json

from ..security.origin import BENCH

CUT = 200


def overview(ctx, limit: int) -> dict:
    rows = ctx.conn.execute(
        "SELECT t.id, t.parent_id, t.contract_json, t.status, t.created_at, ct.conversation_id, ct.position "
        "FROM tasks t LEFT JOIN conversation_tasks ct ON ct.task_id = t.id "
        "WHERE t.origin != ? ORDER BY t.created_at DESC, t.rowid DESC LIMIT ?", (BENCH, limit + 1)).fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit][::-1]
    ids = [r[0] for r in rows]
    steps = dict(ctx.conn.execute(
        f"SELECT task_id, COUNT(*) FROM events WHERE type='model.called' AND task_id IN ({','.join('?' * len(ids))}) "
        "AND COALESCE(json_extract(payload_json, '$.purpose'), 'step') = 'step' GROUP BY task_id", ids).fetchall()) if ids else {}
    tasks = []
    for tid, parent, contract, status, created, cid, _pos in rows:
        goal = (json.loads(contract).get("goal") or "").strip().splitlines()
        tasks.append({"id": tid, "parent_id": parent, "goal": (goal[0] if goal else "")[:CUT], "status": status,
                      "created_at": created, "conversation_id": cid, "steps": steps.get(tid, 0)})
    convs = []
    for cid in dict.fromkeys(r[5] for r in rows if r[5]):
        c = ctx.conn.execute("SELECT title, branch_after FROM conversations WHERE id=?", (cid,)).fetchone() or ("", None)
        order = sorted((r[6], r[0]) for r in rows if r[5] == cid)
        convs.append({"id": cid, "title": c[0], "tasks": [t for _, t in order],
                      "branch_after": c[1] if c[1] not in {t for _, t in order} else None})
    return {"conversations": convs, "tasks": tasks, "truncated": truncated}
