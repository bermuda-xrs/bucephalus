from __future__ import annotations

import json
import sqlite3
import time
import uuid

from .events import sha256_hex

_COLS = ("id, task_id, parent_id, status, opened_reason, seed_artifact, "
         "memory_json, created_at, closed_at")


def _row(r) -> dict:
    keys = [c.strip() for c in _COLS.split(",")]
    return dict(zip(keys, r))


def _sha(memory: dict) -> str:
    return sha256_hex(json.dumps(memory, sort_keys=True))


def current(conn: sqlite3.Connection, task_id: str) -> dict | None:
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
            ORDER BY created_at, rowid""", (task_id,)).fetchall()
    return [_row(r) for r in rows]


def root_session(conn: sqlite3.Connection, task_id: str) -> str | None:
    r = conn.execute(
        """SELECT id FROM sessions WHERE task_id=? AND parent_id IS NULL
           ORDER BY created_at, rowid LIMIT 1""", (task_id,)).fetchone()
    return r[0] if r else None
