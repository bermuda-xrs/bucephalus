from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from .db import chain_lock

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


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
        "compaction.skipped",
        "skill.loaded",
        "delegation.opened",
        "delegation.closed",
        "verify.attempted",
        "verify.passed",
        "verify.failed",
        "verify.unannounced_pass",
        "task.crashed",
        "task.model_stalled",
        "memory.written",
        "memory.consolidated",
        "memory.tampered",
        "tool.search_failed",
        "tool.not_exposed",
        "human.approved",
        "human.uploaded",
        "human.denied",
        "human.steered",
        "integrity.halt",
        "plan.drafted",
        "plan.approved",
        "plan.rejected",
        "review.requested",
        "review.decided",
        "deviation.detected",
        "review.skipped",
        "channel.approved_by",
        "channel.refused",
        "approval.consumed",
        "skills.frozen",
        "session.opened",
        "session.closed",
        "memory.flush_refused",
        "schedule.fired",
        "notification.raised",
        "task.park_seed",
        "task.seed_restored",
        "integrity.verified",
        "plan.pin_verified",
        "tokens.calibration",
        "conversation.history",
        "verify.continued",
        "stuck.warned",
        "stuck.detected",
        "checkpoint.shadow",
        "checkpoint.skipped",
        "task.rewound",
        "agents_md.loaded",
        "agents_md.skipped",
        "hook.ran",
        "hook.denied",
        "hook.stop_blocked",
        "mcp.started",
        "mcp.failed",
        "mcp.skipped",
        "read_first.held",
    }
)


class EventLog:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def append(self, task_id: str, etype: str, payload: dict) -> str:
        if etype not in EVENT_TYPES:
            raise ValueError(f"unknown event type {etype!r}")
        with chain_lock(self.conn):
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
        cur = self.conn.cursor()
        cur.execute(
            """SELECT seq, type, payload_json, prev_hash, hash
               FROM events WHERE task_id=? ORDER BY seq ASC""",
            (task_id,),
        )
        expected_prev = GENESIS
        expected_seq = 0
        for seq, etype, payload_json, prev_hash, h in cur.fetchall():
            if seq != expected_seq:
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
