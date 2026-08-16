"""M3.5 — remembering answers, not just words.

Zero tokens beats cheap tokens. Summarizing the same file twice is pure waste.
A cache hit MUST NOT launder taint.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Callable

from ..kernel.events import canonical, sha256_hex
from ..kernel.types import Taint


def cache_key(op: str, payload: str) -> str:
    normalized = " ".join(payload.split()).strip().lower()
    return sha256_hex(f"{op}\x00{normalized}")


def cached_op(
    conn: sqlite3.Connection,
    op: str,
    payload: str,
    taint: Taint,
    fn: Callable[[], Any],
) -> tuple[Any, bool]:
    """Returns (value, was_hit)."""
    key = cache_key(op, payload)
    row = conn.execute(
        "SELECT value_json, taint FROM semantic_cache WHERE key=?", (key,)
    ).fetchone()
    if row:
        # A cache hit MUST NOT launder taint: serve an entry only into a
        # context at least as tainted as the entry itself. An untrusted-derived
        # value handed to a trusted caller is exactly the laundering step.
        #
        # (The build spec's snippet had this inequality the other way round,
        # which contradicts its own acceptance test — "store an untrusted
        # result, request it in a trusted context, assert miss". The test is
        # the one expressing the intent, so the comparison follows the test.)
        if Taint(row[1]).level <= taint.level:
            conn.execute("UPDATE semantic_cache SET hits=hits+1 WHERE key=?", (key,))
            conn.commit()
            return json.loads(row[0]), True

    value = fn()
    conn.execute(
        """INSERT OR REPLACE INTO semantic_cache(key, value_json, taint,
                                                 source_hash, created_at, hits)
           VALUES (?,?,?,?,?,0)""",
        (key, canonical(value), taint.value, sha256_hex(payload), time.time()),
    )
    conn.commit()
    return value, False


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(hits),0) FROM semantic_cache"
    ).fetchone()
    return {"entries": row[0], "hits": row[1]}
