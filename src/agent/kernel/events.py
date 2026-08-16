"""M1.1 / M1.2 — canonical serialization and the append-only hash chain."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    """Deterministic JSON. Sorted keys, no whitespace, stable float repr.

    NEVER use plain json.dumps for anything that gets hashed.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# The event vocabulary (spec M1.2). Anything not here is a bug, not a feature.
EVENT_TYPES = frozenset(
    {
        "task.created",
        "task.status_changed",
        "task.budget_spent",
        "model.called",
        "model.returned",
        "tool.intent",
        "tool.receipt",
        "artifact.written",
        "artifact.tombstoned",
        "skill.loaded",
        "skill.promoted",
        "skill.demoted",
        "delegation.opened",
        "delegation.closed",
        "verify.attempted",
        "verify.passed",
        "verify.failed",
        "human.approved",
        "human.denied",
        "human.steered",
        "integrity.halt",
        # M13 additions
        "plan.drafted",
        "plan.approved",
        "plan.rejected",
        "step.started",
        "step.completed",
        "review.requested",
        "review.decided",
        "deviation.detected",
    }
)


class EventLog:
    """Append-only. Every state change is an event.

    The application never writes task state directly — it appends an event,
    then projects.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def append(self, task_id: str, etype: str, payload: dict) -> str:
        """Append an event and extend the hash chain. Returns the new hash."""
        if etype not in EVENT_TYPES:
            raise ValueError(f"unknown event type {etype!r}")
        cur = self.conn.cursor()
        cur.execute(
            "SELECT seq, hash FROM events WHERE task_id=? ORDER BY seq DESC LIMIT 1",
            (task_id,),
        )
        row = cur.fetchone()
        seq = (row[0] + 1) if row else 0
        prev_hash = row[1] if row else GENESIS

        body = canonical(
            {
                "task_id": task_id,
                "seq": seq,
                "type": etype,
                "payload": payload,
            }
        )
        h = sha256_hex(prev_hash + body)

        cur.execute(
            """INSERT INTO events(task_id, seq, type, payload_json,
                                  prev_hash, hash, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (task_id, seq, etype, canonical(payload), prev_hash, h, time.time()),
        )
        self.conn.commit()
        return h

    def verify(self, task_id: str) -> tuple[bool, int | None]:
        """Walk the chain. Returns (ok, first_bad_seq)."""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT seq, type, payload_json, prev_hash, hash
               FROM events WHERE task_id=? ORDER BY seq ASC""",
            (task_id,),
        )
        expected_prev = GENESIS
        expected_seq = 0
        for seq, etype, payload_json, prev_hash, h in cur.fetchall():
            if seq != expected_seq:  # a deleted row is tampering too
                return False, seq
            if prev_hash != expected_prev:
                return False, seq
            body = canonical(
                {
                    "task_id": task_id,
                    "seq": seq,
                    "type": etype,
                    "payload": json.loads(payload_json),
                }
            )
            if sha256_hex(prev_hash + body) != h:
                return False, seq
            expected_prev = h
            expected_seq += 1
        return True, None

    def head(self, task_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT hash FROM events WHERE task_id=? ORDER BY seq DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return row[0] if row else None

    def events(self, task_id: str, since_seq: int = -1) -> list[dict]:
        rows = self.conn.execute(
            """SELECT seq, type, payload_json, created_at FROM events
               WHERE task_id=? AND seq>? ORDER BY seq ASC""",
            (task_id, since_seq),
        ).fetchall()
        return [
            {
                "seq": seq,
                "type": etype,
                "payload": json.loads(payload_json),
                "created_at": created_at,
            }
            for seq, etype, payload_json, created_at in rows
        ]

    def last_of_type(self, task_id: str, etype: str) -> dict | None:
        row = self.conn.execute(
            """SELECT seq, payload_json FROM events
               WHERE task_id=? AND type=? ORDER BY seq DESC LIMIT 1""",
            (task_id, etype),
        ).fetchone()
        if not row:
            return None
        return {"seq": row[0], "payload": json.loads(row[1])}
