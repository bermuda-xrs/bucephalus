from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from .events import EventLog


class IntegrityError(RuntimeError):
    ...


def _is_chain_hash(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class Anchor:
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
        return self.chain_heads().get(task_id)

    def chain_heads(self) -> dict[str, str]:
        heads = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and _is_chain_hash(parts[2]):
                heads[parts[1]] = parts[2]
        return heads

    def all_tasks(self) -> set[str]:
        out: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                out.add(parts[1])
        return out


TOMBSTONE = "tombstone:"
REVOKED = "revoked"
ANCHOR_BEHIND = "anchor_behind"
ANCHOR_REWRITTEN = "anchor_rewritten"


def classify_anchor(conn: sqlite3.Connection, log: EventLog, anchor: Anchor,
                    task_id: str) -> str | None:
    return _classify(conn, log, task_id, anchor.latest_chain(task_id))


def _classify(conn: sqlite3.Connection, log: EventLog, task_id: str,
              anchored: str | None) -> str | None:
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
    ok, bad_seq = log.verify(task_id)
    if not ok:
        try:
            log.append(
                task_id, "integrity.halt", {"reason": "chain_break", "seq": bad_seq}
            )
        except Exception:  # noqa: BLE001
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


def checkpoint(log: EventLog, anchor: Anchor, task_id: str) -> None:
    head = log.head(task_id)
    if head:
        anchor.record(task_id, head)


def verify_memory(store) -> dict | None:
    from ..memory.store import INTACT, TAMPERED, check_fingerprint

    state = check_fingerprint(store)
    if state == INTACT:
        return None
    detail = (
        "tier 1 memory does not match its last blessed fingerprint — the "
        "files were changed outside memory_write, memory_consolidate or "
        "`bucephalus memory`. Tier 1 is REFUSED on every task until this is "
        "resolved; tasks still run, without persistent memory. Inspect with "
        "`bucephalus memory`, then `bucephalus memory --accept` to bless the current "
        "state or `--forget` to remove entries."
    ) if state == TAMPERED else (
        "tier 1 memory has content but no blessed fingerprint. Either it "
        "predates the anchor record or it was placed there by something that "
        "did not go through the write path; both are handled as tampering, "
        "because text that cannot be accounted for still goes into every "
        "future prompt. `bucephalus memory --accept` to bless it."
    )
    return {"task_id": "__memory__", "severity": "critical", "detail": detail}


def verify_all(conn: sqlite3.Connection, log: EventLog, anchor: Anchor,
               memory=None) -> list[dict]:
    findings = []
    if memory is not None:
        found = verify_memory(memory)
        if found:
            findings.append(found)
    known = {r[0] for r in conn.execute("SELECT id FROM tasks").fetchall()}
    marked = anchor.all_tasks()
    try:  # a record from before removals has no table; this check never migrates what it reads
        removed = dict(conn.execute("SELECT task_id, head FROM task_tombstones").fetchall())
    except sqlite3.OperationalError:
        removed = {}
    for task_id in sorted(t[len(TOMBSTONE):] for t in marked if t.startswith(TOMBSTONE)):
        if task_id in known and anchor.latest(TOMBSTONE + task_id) != REVOKED:
            findings.append({"task_id": task_id, "severity": "info",
                             "detail": "a removal of this task did not finish; it is still here, so delete it again"})
    for task_id in sorted(marked - known):
        if task_id.startswith(TOMBSTONE):
            continue
        last = anchor.latest(TOMBSTONE + task_id)
        if last and last != REVOKED and removed.get(task_id) == last:
            findings.append({"task_id": task_id, "severity": "info",
                             "detail": f"removed on purpose from the console; the anchor holds its last hash "
                                       f"({last[:12]}) and nothing of its content"})
            continue
        if not task_id.startswith(("plan:", "schedule:")):
            findings.append({"task_id": task_id, "severity": "critical",
                             "detail": "the anchor has a checkpoint for this task and the "
                                       "database has no record of it - deleted after it "
                                       "was checkpointed"})
    from ..schedule import seal

    try:  # a record from before schedules has no table; this check never migrates what it reads
        rows = conn.execute("SELECT id, spec, contract_json, budget_json FROM schedules "
                            "WHERE removed_at IS NULL").fetchall()
    except sqlite3.OperationalError:
        rows = []
    for r in rows:
        s = dict(zip(("id", "spec", "contract_json", "budget_json"), r))
        anchored = anchor.latest(f"schedule:{s['id']}")
        if anchored and anchored != seal(s):
            findings.append({"task_id": f"schedule:{s['id']}", "severity": "critical",
                             "detail": f"schedule {s['id']} changed since it was created, so it will not fire; "
                                       "remove it and add it again if the change is yours"})
    heads = anchor.chain_heads()
    for task_id in sorted(known):
        ok, bad = log.verify(task_id)
        if not ok:
            findings.append(
                {"task_id": task_id, "severity": "critical", "detail": f"chain break at seq {bad}"}
            )
            continue
        state = _classify(conn, log, task_id, heads.get(task_id))
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
                               "checkpoint"),
                }
            )
    return findings


def critical(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f.get("severity") == "critical"]
