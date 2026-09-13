"""B1 - sessions: one context-window lifetime of one task attempt.

A long task reaches a BOUNDARY where its tail is compacted. At that boundary
its session row is closed and a child opens, with a fresh tier-1 snapshot and
a record of what seeded it. A lineage chain, never a transcript rewritten in
place: the parent row keeps what the parent had, the child starts from what
the boundary left.

WHAT A SESSION IS NOT. It is not a conversation spanning tasks, and it never
spans ATTEMPTS. A child's task is its parent's task, enforced by a trigger in
the schema so that no code path, present or future, can seed one attempt's
session from another's (9.7). Delegated subtasks are separate tasks with their
own sessions.

WHAT A SESSION HOLDS THAT A PROCESS USED TO. The tier-1 snapshot its prefix is
built from. That was a dict entry on the context, so a task resumed in another
process re-read memory as it stood NOW - writes made after the task started,
which Phase 3 promised would stay invisible until the next boundary, appeared
early. On the row, the snapshot lives exactly as long as its session.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid

_COLS = ("id, task_id, parent_id, status, opened_reason, seed_artifact, "
         "memory_json, created_at, closed_at")


def _row(r) -> dict:
    keys = [c.strip() for c in _COLS.split(",")]
    return dict(zip(keys, r))


def _sha(memory: dict) -> str:
    return hashlib.sha256(
        json.dumps(memory, sort_keys=True).encode("utf-8")).hexdigest()


def current(conn: sqlite3.Connection, task_id: str) -> dict | None:
    """The task's open session. The schema allows at most one."""
    r = conn.execute(
        f"SELECT {_COLS} FROM sessions WHERE task_id=? AND status='open'",
        (task_id,)).fetchone()
    return _row(r) if r else None


def open_session(conn: sqlite3.Connection, log, task_id: str, memory: dict,
                 parent_id: str | None = None, reason: str = "start",
                 seed_artifact: str | None = None,
                 detail: dict | None = None) -> str:
    sid = "s_" + uuid.uuid4().hex[:16]
    conn.execute(
        f"""INSERT INTO sessions({_COLS})
            VALUES (?,?,?,?,?,?,?,?,NULL)""",
        (sid, task_id, parent_id, "open", reason, seed_artifact,
         json.dumps(memory, sort_keys=True), time.time()))
    conn.commit()
    log.append(task_id, "session.opened", {
        "session_id": sid, "parent_id": parent_id, "reason": reason,
        "seed_artifact": seed_artifact, "memory_sha256": _sha(memory),
        **(detail or {})})
    return sid


def close(conn: sqlite3.Connection, log, session_id: str, reason: str) -> None:
    r = conn.execute("SELECT task_id, status FROM sessions WHERE id=?",
                     (session_id,)).fetchone()
    if r is None or r[1] != "open":
        return
    conn.execute("UPDATE sessions SET status='closed', closed_at=? WHERE id=?",
                 (time.time(), session_id))
    conn.commit()
    log.append(r[0], "session.closed", {"session_id": session_id,
                                        "reason": reason})


def lineage(conn: sqlite3.Connection, session_id: str) -> list[str]:
    """Root first, this session last.

    Never crosses a task: the schema will not let a parent live in another.
    Cannot cycle either - a parent must exist before its child is inserted,
    and nothing updates `parent_id`.
    """
    out, sid = [], session_id
    while sid:
        out.append(sid)
        r = conn.execute("SELECT parent_id FROM sessions WHERE id=?",
                         (sid,)).fetchone()
        sid = r[0] if r else None
    return list(reversed(out))


def for_task(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {_COLS} FROM sessions WHERE task_id=?
            ORDER BY created_at, id""", (task_id,)).fetchall()
    return [_row(r) for r in rows]


def root_session(conn: sqlite3.Connection, task_id: str) -> str | None:
    """A task's first session: the stable name of its whole lineage."""
    r = conn.execute(
        """SELECT id FROM sessions WHERE task_id=? AND parent_id IS NULL
           ORDER BY created_at, id LIMIT 1""", (task_id,)).fetchone()
    return r[0] if r else None
