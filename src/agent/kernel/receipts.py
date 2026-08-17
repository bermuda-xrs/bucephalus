"""M1.6 — do things exactly once.

A checkpoint tells you WHERE the agent was, not WHAT it already did. Every
side-effecting tool routes through execute_once or the guarantee is void.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Callable

from .db import chain_lock
from .events import canonical, sha256_hex


def idempotency_key(task_id: str, step_seq: int, tool: str, args: dict) -> str:
    return sha256_hex(
        canonical({"task": task_id, "step": step_seq, "tool": tool, "args": args})
    )


def execute_once(
    conn: sqlite3.Connection,
    key: str,
    tool_name: str,
    args: dict,
    fn: Callable[[], dict],
) -> dict:
    """Run fn() at most once across all replays.

    The check and the write are one critical section. Two threads reaching a
    side-effecting tool with the same key concurrently would both see no
    receipt and both perform the effect — the exact duplicate-send this
    function exists to prevent.
    """
    with chain_lock():
        cur = conn.cursor()
        cur.execute("SELECT result_json FROM receipts WHERE idempotency_key=?", (key,))
        row = cur.fetchone()
        if row:
            return json.loads(row[0])  # already done — do NOT re-run

        result = fn()

        cur.execute(
            """INSERT INTO receipts(idempotency_key, tool_name, args_hash,
                                    result_json, created_at)
               VALUES (?,?,?,?,?)""",
            (key, tool_name, sha256_hex(canonical(args)), canonical(result), time.time()),
        )
        conn.commit()
        return result


def has_receipt(conn: sqlite3.Connection, key: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM receipts WHERE idempotency_key=?", (key,)
        ).fetchone()
        is not None
    )
