"""M1.4 / M1.5 — the anchor and the resume protocol.

The chain proves internal consistency. It does not stop someone rewriting the
whole log and recomputing every hash. For that you need an anchor OUTSIDE the
agent's write scope.

Halting is correct behavior. There is deliberately no auto-repair path.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from .events import EventLog


class IntegrityError(RuntimeError):
    """Raised when the chain or the anchor disagrees with the database."""


def _is_chain_hash(value: str) -> bool:
    """A sha256 hex digest, which is what `checkpoint()` writes and nothing
    else in the anchor file looks like."""
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class Anchor:
    """Stores the latest known-good chain head per task.

    MUST live outside any directory the agent can write to.
    The sandbox (M5) MUST deny writes to anchor_path.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def record(self, task_id: str, head_hash: str) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"{time.time()}\t{task_id}\t{head_hash}\n")
            f.flush()
            os.fsync(f.fileno())

    def latest(self, task_id: str) -> str | None:
        latest = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            _, tid, h = parts
            if tid == task_id:
                latest = h
        return latest

    def latest_chain(self, task_id: str) -> str | None:
        """The last CHAIN HEAD recorded for this task, ignoring anything else.

        The anchor file is a flat (time, key, value) log, and for a long time
        two different kinds of record shared one keyspace: `checkpoint()`
        wrote a chain head under the task id, and plan approval wrote
        `plan:<hash>` under the SAME task id. Whichever came last won.

        That broke both readers, in opposite directions:

          * the resume check saw a `plan:` value, could not find it among the
            task's event hashes, and concluded the history had been rewritten
          * `verify_plan_pin` saw a chain head, found it did not start with
            `plan:`, and SILENTLY SKIPPED its own anchor check — and since
            `run_task` checkpoints on every run, the plan pin stopped being
            the latest entry after the first step, so that check has
            effectively never fired

        Plan pins now live under their own key. This filter stays because
        existing anchor files still hold the old entries, and a migration to
        fix a log that is append-only by design is worse than a reader that
        knows what a chain head looks like: 64 hex characters.
        """
        latest = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            parts = line.split("	")
            if len(parts) != 3:
                continue
            _, tid, value = parts
            if tid == task_id and _is_chain_hash(value):
                latest = value
        return latest

    def all_tasks(self) -> set[str]:
        out: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                out.add(parts[1])
        return out


# Two conditions that look identical from `head != anchor` and are opposites.
#
# D17: 19 of 283 tasks in the live database reported `severity: critical - head
# does not match anchor` while every chain verified and nothing was tampered.
# They had simply continued past their last checkpoint, because bench runs do
# not checkpoint per task (D10, still open, and the DISEASE of which this is
# the symptom). Nineteen permanent criticals is how a real one gets scrolled
# past - and Phase 3 puts memory files under this same chain, so a
# memory-tampering alert would have arrived as the twentieth entry in a list
# nobody reads.
#
# The discriminator is whether the anchored hash is STILL IN the chain:
#
#   * present  -> the chain legitimately extended past the checkpoint. The
#                 anchor is behind, not wrong. Nothing was rewritten, because
#                 the hash we anchored is still exactly where we left it.
#   * absent   -> the history that contained it is gone. That is a rewrite or
#                 a fork, and it is the condition the anchor exists to catch.
#
# Note the direction of the weakening test: this can only ever DOWNGRADE a
# finding whose anchored hash is provably still in the log. A tampered chain
# has no such hash, so it cannot reach the quieter branch.
ANCHOR_BEHIND = "anchor_behind"
ANCHOR_REWRITTEN = "anchor_rewritten"


def classify_anchor(conn: sqlite3.Connection, log: EventLog, anchor: Anchor,
                    task_id: str) -> str | None:
    """None when the anchor agrees with the head, or there is no anchor."""
    anchored = anchor.latest_chain(task_id)
    if not anchored:
        return None
    if log.head(task_id) == anchored:
        return None
    still_present = conn.execute(
        "SELECT 1 FROM events WHERE task_id = ? AND hash = ? LIMIT 1",
        (task_id, anchored),
    ).fetchone()
    return ANCHOR_BEHIND if still_present else ANCHOR_REWRITTEN


def resume(
    conn: sqlite3.Connection, task_id: str, log: EventLog, anchor: Anchor
) -> None:
    """Verify before continuing. Mismatch halts and asks a human."""
    ok, bad_seq = log.verify(task_id)
    if not ok:
        # Recording the halt extends the chain from the last good link; the
        # break itself stays visible at bad_seq.
        try:
            log.append(
                task_id, "integrity.halt", {"reason": "chain_break", "seq": bad_seq}
            )
        except Exception:  # noqa: BLE001 - the log itself may be the damaged thing
            pass
        raise IntegrityError(f"Chain broken at seq {bad_seq}")

    db_head = log.head(task_id)
    anchored = anchor.latest_chain(task_id)
    if classify_anchor(conn, log, anchor, task_id) == ANCHOR_REWRITTEN:
        raise IntegrityError(
            f"Head does not match anchor and the anchored hash is GONE from "
            f"the log — history was rewritten "
            f"(db={str(db_head)[:12]}, anchor={str(anchored)[:12]})"
        )
    # An anchor that is merely BEHIND is not a reason to refuse to resume: the
    # anchored hash is still in the chain, so nothing was rewritten and the
    # task simply ran past its last checkpoint. Halting here would make every
    # un-checkpointed task unresumable, which is a denial of service built out
    # of a bookkeeping gap.
    # Now safe to project state and continue.


def checkpoint(log: EventLog, anchor: Anchor, task_id: str) -> None:
    """Write an anchor entry. Called at every status change and human approval."""
    head = log.head(task_id)
    if head:
        anchor.record(task_id, head)


def verify_memory(store) -> dict | None:
    """Tier 1 against its last blessed fingerprint.

    CRITICAL when it disagrees, and the severity is the point of D17 having
    been cleared first: this alert arrives into an empty channel instead of
    as the twentieth entry in a list nobody reads. Tier 1 is in the prefix of
    every future session, so an unaccounted change to it is an unaccounted
    change to what the agent IS.
    """
    from ..memory.store import INTACT, TAMPERED, check_fingerprint

    state = check_fingerprint(store)
    if state == INTACT:
        return None
    detail = (
        "tier 1 memory does not match its last blessed fingerprint — the "
        "files were changed outside memory_write, memory_consolidate or "
        "`alpha memory`. Tier 1 is REFUSED on every task until this is "
        "resolved; tasks still run, without persistent memory. Inspect with "
        "`alpha memory`, then `alpha memory --accept` to bless the current "
        "state or `--forget` to remove entries."
    ) if state == TAMPERED else (
        "tier 1 memory has content but no blessed fingerprint. Either it "
        "predates the anchor record or it was placed there by something that "
        "did not go through the write path; both are handled as tampering, "
        "because text that cannot be accounted for still goes into every "
        "future prompt. `alpha memory --accept` to bless it."
    )
    return {"task_id": "__memory__", "severity": "critical", "detail": detail}


def verify_all(conn: sqlite3.Connection, log: EventLog, anchor: Anchor,
               memory=None) -> list[dict]:
    """Sweep every task. Used by `alpha verify` and the /alerts endpoint."""
    findings = []
    if memory is not None:
        found = verify_memory(memory)
        if found:
            findings.append(found)
    for (task_id,) in conn.execute("SELECT id FROM tasks").fetchall():
        ok, bad = log.verify(task_id)
        anchored = anchor.latest(task_id)
        head = log.head(task_id)
        if not ok:
            findings.append(
                {"task_id": task_id, "severity": "critical", "detail": f"chain break at seq {bad}"}
            )
            continue
        state = classify_anchor(conn, log, anchor, task_id)
        if state == ANCHOR_REWRITTEN:
            findings.append(
                {
                    "task_id": task_id,
                    "severity": "critical",
                    "detail": ("head does not match anchor and the anchored "
                               "hash is GONE from the log — history rewritten"),
                }
            )
        elif state == ANCHOR_BEHIND:
            findings.append(
                {
                    "task_id": task_id,
                    "severity": "info",
                    "detail": ("anchor is behind the head; the anchored hash "
                               "is still in the chain, so nothing was "
                               "rewritten. The task ran past its last "
                               "checkpoint — DEFERRED D10"),
                }
            )
    return findings


def critical(findings: list[dict]) -> list[dict]:
    """The channel that must stay empty. An alarm with a permanent backlog is
    not an alarm, which is the whole of D17."""
    return [f for f in findings if f.get("severity") == "critical"]
