from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Callable

from .db import chain_lock
from .events import canonical, sha256_hex


def idempotency_key(task_id: str, step_seq: int, tool: str, args: dict) -> str:
    return sha256_hex(
        canonical({"task": task_id, "step": step_seq, "tool": tool, "args": args})
    )


REWIND_STRIDE = 1_000_000


def rewind_epoch(conn: sqlite3.Connection, task_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND type='task.rewound'",
                        (task_id,)).fetchone()[0] * REWIND_STRIDE


_CLAIM = "CLAIMED"
_inflight: dict[str, threading.Event] = {}
WAIT_S = 900.0
log = logging.getLogger("agent.receipts")


def outcome_unknown(key: str) -> dict:
    return {"ok": False, "outcome_unknown": True,
            "error": ("this action was started by an earlier run that stopped before "
                      "recording its result, so it may or may not have happened; "
                      "check before repeating it (receipt " + key[:12] + ")")}


def execute_once(
    conn: sqlite3.Connection,
    key: str,
    tool_name: str,
    args: dict,
    fn: Callable[[], dict],
) -> dict:
    with chain_lock(conn):
        row = conn.execute(
            "SELECT result_json FROM receipts WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO receipts(idempotency_key, tool_name, args_hash,
                                        result_json, created_at) VALUES (?,?,?,?,?)""",
                (key, tool_name, sha256_hex(canonical(args)), _CLAIM, time.time()))
            conn.commit()
            done = _inflight[key] = threading.Event()
            mine = True
        else:
            mine = False
            if row[0] != _CLAIM:
                return json.loads(row[0])
            done = _inflight.get(key)
    if not mine:
        if done is None:
            return outcome_unknown(key)
        if not done.wait(WAIT_S):
            return outcome_unknown(key)
        return execute_once(conn, key, tool_name, args, fn)

    try:
        result = fn()
    except Exception:
        with chain_lock(conn):
            conn.execute("DELETE FROM receipts WHERE idempotency_key=? AND result_json=?",
                         (key, _CLAIM))
            conn.commit()
            _inflight.pop(key, None)
        done.set()
        raise
    except BaseException:
        _inflight.pop(key, None)
        done.set()
        raise
    try:
        with chain_lock(conn):
            conn.execute("UPDATE receipts SET result_json=? WHERE idempotency_key=?",
                         (canonical(result), key))
            conn.commit()
    except Exception:  # noqa: BLE001
        log.exception("receipt %s: the effect ran but its result could not be recorded", key[:12])
    finally:
        _inflight.pop(key, None)
        done.set()
    return result
