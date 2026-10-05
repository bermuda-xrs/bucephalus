from __future__ import annotations

import sqlite3


def task_tree(conn: sqlite3.Connection, task_id: str) -> set[str]:
    root = conn.execute(
        """WITH RECURSIVE up(id, parent_id) AS (
               SELECT id, parent_id FROM tasks WHERE id = ?
               UNION ALL
               SELECT t.id, t.parent_id FROM tasks t JOIN up ON t.id = up.parent_id
           )
           SELECT id FROM up WHERE parent_id IS NULL LIMIT 1""",
        (task_id,),
    ).fetchone()
    root_id = root[0] if root else task_id
    rows = conn.execute(
        """WITH RECURSIVE down(id) AS (
               SELECT id FROM tasks WHERE id = ?
               UNION ALL
               SELECT t.id FROM tasks t JOIN down ON t.parent_id = down.id
           )
           SELECT id FROM down""",
        (root_id,),
    ).fetchall()
    return {r[0] for r in rows} | {task_id}


def visible_tasks(ctx, task) -> set[str] | None:
    if not getattr(ctx.config, "isolate_retrieval", False):
        return None
    if task is None:
        return set()
    return task_tree(ctx.conn, task.id)


def isolation_state(ctx) -> dict:
    on = bool(getattr(ctx.config, "isolate_retrieval", False))
    return {
        "isolate_retrieval": on,
        "search_history": "scoped to the task tree" if on else "all sessions",
        "expand_artifact": "scoped to the task tree" if on else "all tasks",
        "memory_tier1": "unavailable" if on else "user data dir",
        "note": (
            "a benchmark attempt can read only what it produced itself; "
            "cross-arm and cross-run retrieval are impossible by construction"
            if on else
            "unrestricted retrieval — correct for normal use, INVALID for a "
            "paired trial"
        ),
    }


def wrote_artifact(conn: sqlite3.Connection, task_ids: set[str],
                   artifact_id: str) -> bool:
    if not task_ids:
        return False
    marks = ",".join("?" * len(task_ids))
    row = conn.execute(
        f"""SELECT 1 FROM events
            WHERE type = 'artifact.written'
              AND task_id IN ({marks})
              AND json_extract(payload_json, '$.id') = ?
            LIMIT 1""",
        (*sorted(task_ids), artifact_id),
    ).fetchone()
    return row is not None
