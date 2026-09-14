"""M1.1 / M1.2 — canonical serialization and the append-only hash chain."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from .db import chain_lock

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
        # The artifact verified on a terminal path without the model
        # ever emitting <final>. A pass, recorded distinctly so
        # "did the work" and "said it was done" stay separable.
        "verify.unannounced_pass",
        "task.crashed",
        # A stalled model call: a MACHINE fault, not a task outcome.
        # Distinct from task.crashed precisely so the two can never be
        # confused again - a stall recorded as a crash is scored as a
        # task failure, which is what aborted two Phase 2A attempts.
        "task.model_stalled",
        # A1. Tier 1 is rendered into the stable prefix of every future
        # session, so a change to it is a change to what the agent IS. The
        # event carries a fingerprint over both memory FILES, which is what
        # lets a modified MEMORY.md fail verification like any other state -
        # over the files rather than the parsed entries, because a tampered
        # line that fails to parse would otherwise be invisible, and that is
        # precisely the line an attacker would write.
        "memory.written",
        "memory.consolidated",
        # Tier 1 did not match its last blessed fingerprint. CRITICAL: this
        # text goes into every future prompt, so an unaccounted change to it
        # is an unaccounted change to what the agent is.
        "memory.tampered",
        # A scraped source fetched and parsed to nothing. Distinct from a
        # search that honestly matched nothing, because a broken parser
        # otherwise reads as a topic with no coverage - and G4 would ground a
        # skill on it and call the skill grounded.
        "tool.search_failed",
        # B3. A tool call refused because this agent is not permitted the
        # tool at all, as distinct from a capability-gate denial about its
        # ARGUMENTS. Recorded because the refusal is the auditable behaviour:
        # an agent that cannot SEE a tool works around it silently.
        "tool.not_exposed",
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
        # A4. The deputy was SKIPPED because this task came over a channel.
        # Recorded rather than silent: "no review event" and "review declined
        # on principle" look identical in a log that only records what ran,
        # and the second is the claim the phase is making.
        "review.skipped",
        # A4.3. Who pressed the button, re-verified against the allowlist at
        # the moment of the decision. The approval row says WHAT was decided;
        # this says WHO, on a channel where the person deciding need not be
        # the person who asked.
        "channel.approved_by",
        # A4.3. A decision arriving over a channel from a sender the
        # allowlist no longer contains, or on an approval already decided.
        "channel.refused",
        # A human decision ACTED ON by the resumed task: executed, voided
        # because policy narrowed since, or a denial handed back to the model.
        # The approval row says what was decided; this says what came of it.
        "approval.consumed",
        # The skill set a task's prefix is built from, INCLUDING the empty set,
        # recorded once when it is frozen. For a paired trial this is the ARM
        # ASSIGNMENT, and it used to live only in process memory.
        "skills.frozen",
        # B1. A session boundary: one closes, its child opens. The opening
        # event carries what happened AT the boundary - the flush outcome,
        # what compaction tombstoned, whether tier 1 changed.
        "session.opened",
        "session.closed",
        # 9.4. A fact offered to tier 1 in the pre-compaction flush and
        # refused. Recorded because the alternative is a fact that vanished at
        # compression with no record of why.
        "memory.flush_refused",
        # A3. This task was created by a schedule: which one, and which
        # occurrence. What lets scheduled runs separate from interactive ones.
        "schedule.fired",
        # 11.2. A task that parked with nobody watching, and the notification
        # raised for it.
        "notification.raised",
        # A3-1. Two checks that pass SILENTLY left nothing to point at: a real
        # run's log could show that they did not fail, never that they ran.
        # Each now records what it verified.
        "integrity.verified",
        "plan.pin_verified",
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
        """Append an event and extend the hash chain. Returns the new hash.

        Reading the head and writing the successor must be atomic: two threads
        interleaving here would both read the same prev_hash and fork the
        chain, which verify() would then report as tampering.
        """
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
