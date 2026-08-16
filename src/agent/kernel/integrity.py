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

    def all_tasks(self) -> set[str]:
        out: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                out.add(parts[1])
        return out


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
    anchored = anchor.latest(task_id)
    if anchored and db_head != anchored:
        raise IntegrityError(
            f"Head does not match anchor — log may be rewritten "
            f"(db={str(db_head)[:12]}, anchor={anchored[:12]})"
        )
    # Now safe to project state and continue.


def checkpoint(log: EventLog, anchor: Anchor, task_id: str) -> None:
    """Write an anchor entry. Called at every status change and human approval."""
    head = log.head(task_id)
    if head:
        anchor.record(task_id, head)


def verify_all(conn: sqlite3.Connection, log: EventLog, anchor: Anchor) -> list[dict]:
    """Sweep every task. Used by `alpha verify` and the /alerts endpoint."""
    findings = []
    for (task_id,) in conn.execute("SELECT id FROM tasks").fetchall():
        ok, bad = log.verify(task_id)
        anchored = anchor.latest(task_id)
        head = log.head(task_id)
        if not ok:
            findings.append(
                {"task_id": task_id, "severity": "critical", "detail": f"chain break at seq {bad}"}
            )
        elif anchored and head != anchored:
            findings.append(
                {
                    "task_id": task_id,
                    "severity": "critical",
                    "detail": "head does not match anchor",
                }
            )
    return findings
