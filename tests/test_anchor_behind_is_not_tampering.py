from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.kernel.db import open_db  # noqa: E402
from agent.kernel.events import EventLog  # noqa: E402
from agent.kernel.integrity import (  # noqa: E402
    ANCHOR_BEHIND,
    ANCHOR_REWRITTEN,
    Anchor,
    IntegrityError,
    checkpoint,
    classify_anchor,
    critical,
    resume,
    verify_all,
)


@pytest.fixture()
def rig(tmp_path):
    conn = open_db(tmp_path / "d.db")
    log = EventLog(conn)
    anchor = Anchor(tmp_path / "chain.anchor")
    import json
    import time
    conn.execute(
        """INSERT INTO tasks(id, parent_id, contract_json, status, budget_json,
                             created_at, updated_at)
           VALUES ('t1', NULL, ?, 'running', '{}', ?, ?)""",
        (json.dumps({"goal": "g"}), time.time(), time.time()))
    conn.commit()
    yield conn, log, anchor
    conn.close()


def advance(log, n=3):
    for i in range(n):
        log.append("t1", "task.budget_spent", {"i": i})


def test_an_anchor_that_is_merely_behind_is_not_critical(rig):
    conn, log, anchor = rig
    advance(log, 2)
    checkpoint(log, anchor, "t1")
    advance(log, 3)

    assert classify_anchor(conn, log, anchor, "t1") == ANCHOR_BEHIND
    findings = verify_all(conn, log, anchor)
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"
    assert "still in the chain" in findings[0]["detail"]
    assert critical(findings) == []


def test_a_rewritten_history_is_still_critical(rig):
    conn, log, anchor = rig
    advance(log, 2)
    checkpoint(log, anchor, "t1")
    anchored = anchor.latest("t1")
    assert conn.execute(
        "SELECT 1 FROM events WHERE task_id='t1' AND hash=?", (anchored,)
    ).fetchone(), "the anchored hash must start out present"

    seq = conn.execute("SELECT seq FROM events WHERE task_id='t1' AND hash=?",
                       (anchored,)).fetchone()[0]
    conn.execute("DELETE FROM events WHERE task_id='t1' AND seq >= ?", (seq,))
    conn.commit()
    advance(log, 2)

    assert classify_anchor(conn, log, anchor, "t1") == ANCHOR_REWRITTEN
    findings = verify_all(conn, log, anchor)
    assert any(f["severity"] == "critical" for f in findings), (
        "a rewrite must still be critical; the downgrade may only ever apply "
        "to an anchored hash that is provably still in the log")
    assert critical(findings)


def test_resume_refuses_a_rewrite_but_allows_a_behind_anchor(rig):
    conn, log, anchor = rig
    advance(log, 2)
    checkpoint(log, anchor, "t1")
    advance(log, 3)
    resume(conn, "t1", log, anchor)

    anchored = anchor.latest("t1")
    seq = conn.execute("SELECT seq FROM events WHERE task_id='t1' AND hash=?",
                       (anchored,)).fetchone()[0]
    conn.execute("DELETE FROM events WHERE task_id='t1' AND seq >= ?", (seq,))
    conn.commit()
    advance(log, 1)
    with pytest.raises(IntegrityError, match="rewritten"):
        resume(conn, "t1", log, anchor)


def test_a_chain_break_outranks_the_anchor_question(rig):
    conn, log, anchor = rig
    advance(log, 3)
    checkpoint(log, anchor, "t1")
    conn.execute("UPDATE events SET payload_json='{\"tampered\":1}' "
                 "WHERE task_id='t1' AND seq=1")
    conn.commit()

    findings = verify_all(conn, log, anchor)
    assert len(findings) == 1, "one finding per task, the most severe"
    assert findings[0]["severity"] == "critical"
    assert "chain break" in findings[0]["detail"]


def test_an_agreeing_anchor_reports_nothing(rig):
    conn, log, anchor = rig
    advance(log, 3)
    checkpoint(log, anchor, "t1")
    assert classify_anchor(conn, log, anchor, "t1") is None
    assert verify_all(conn, log, anchor) == []


def test_the_live_database_has_an_empty_critical_channel():
    db = ROOT / "agent.local.db"
    if not db.exists():
        pytest.skip("no local database in this checkout")
    findings = _live_findings(db, ROOT / "anchor" / "chain.local.anchor")
    assert critical(findings) == [], (
        f"the critical channel must be empty: {critical(findings)[:3]}")


def _live_findings(db, anchor_path):
    import sqlite3

    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return verify_all(conn, EventLog(conn), Anchor(anchor_path))
    finally:
        conn.close()


def test_checking_the_live_record_does_not_change_it(tmp_path):
    import hashlib
    import shutil

    old = tmp_path / "record.db"
    shutil.copyfile(ROOT / "docs" / "evidence" / "g1-ablation-2026-09-17.db", old)
    before = hashlib.sha256(old.read_bytes()).hexdigest()
    _live_findings(old, tmp_path / "chain.anchor")
    assert hashlib.sha256(old.read_bytes()).hexdigest() == before
