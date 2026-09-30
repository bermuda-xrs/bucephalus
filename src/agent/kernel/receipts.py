"""M1.6 — do things exactly once.

A checkpoint tells you WHERE the agent was, not WHAT it already did. Every
side-effecting tool routes through execute_once or the guarantee is void.
"""

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


# A receipt row whose result is this was CLAIMED - the effect was about to
# start - and never filled in: whoever claimed it stopped somewhere inside.
_CLAIM = canonical({"claimed": True})
_inflight: dict[str, threading.Event] = {}      # keys being run by THIS process
WAIT_S = 900.0        # the longest a same-key caller waits for the thread running it
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
    """Run fn() at most once across all replays. Claim, execute, fill in (D27).

    The receipt is CLAIMED - a row, committed and fsynced - before the effect
    runs, and filled in after. Holding the chain lock across the effect
    instead froze every event append in the process for as long as a push or a
    send took, and a crash between the effect and its receipt left NO trace,
    so a replay did it again. Now:

      * a completed receipt is returned, the effect not re-run;
      * a claim nobody in this process is running means a run stopped inside
        the effect: the outcome is UNKNOWN and is reported as such - never
        repeated, because a second send is worse than one that needs a look;
      * two threads with the same key: the second waits for the first's result;
      * an effect that RAISES releases its claim and can be tried again, as
        before; one cut off by the process dying leaves the claim.
    """
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
                return json.loads(row[0])                 # already done - do NOT re-run
            done = _inflight.get(key)
    if not mine:
        if done is None:
            return outcome_unknown(key)                   # claimed by a run that is gone
        if not done.wait(WAIT_S):                         # never wait forever
            return outcome_unknown(key)
        return execute_once(conn, key, tool_name, args, fn)

    try:
        result = fn()
    except Exception:
        with chain_lock(conn):                            # it failed: free the claim
            conn.execute("DELETE FROM receipts WHERE idempotency_key=? AND result_json=?",
                         (key, _CLAIM))
            conn.commit()
            _inflight.pop(key, None)
        done.set()
        raise
    except BaseException:
        _inflight.pop(key, None)                          # cut off: the claim stays
        done.set()
        raise
    try:
        with chain_lock(conn):
            conn.execute("UPDATE receipts SET result_json=? WHERE idempotency_key=?",
                         (canonical(result), key))
            conn.commit()
    except Exception:  # noqa: BLE001 - the effect HAPPENED; nothing here may hide that
        # AUDIT-ITERATION-3B M2. Another writer past busy_timeout: the row
        # stays CLAIMED - honestly "outcome unknown" on disk - but the caller
        # is told what happened, and the waiters are released in the finally
        # below. It used to raise here, leaving a stale Event that a same-key
        # retry (a resumed task's first action) waited on forever.
        log.exception("receipt %s: the effect ran but its result could not be recorded", key[:12])
    finally:
        _inflight.pop(key, None)
        done.set()
    return result


def has_receipt(conn: sqlite3.Connection, key: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM receipts WHERE idempotency_key=?", (key,)
        ).fetchone()
        is not None
    )
