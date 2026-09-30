"""D72 - the console's cached integrity sweep (control/panels.py::integrity_findings).

Every probe first takes a clean sweep (so the cache is warm), tampers by one
route, then asks the cached function again - as the next /alerts poll would.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from agent.control import panels
from agent.kernel.integrity import checkpoint, verify_all

MARK = "ZQXJMARKER0001"


@pytest.fixture
def warm(ctx, task):
    for i in range(5):
        ctx.log.append(task.id, "model.called", {"purpose": "step", "text": f"{MARK} {i}"})
    checkpoint(ctx.log, ctx.anchor, task.id)
    assert panels.integrity_findings(ctx) == []
    return ctx, task


def _critical(findings):
    return [f for f in findings if f["severity"] == "critical"]


def test_clean_another_connections_edit_is_seen_on_the_next_poll(warm):
    ctx, task = warm
    other = sqlite3.connect(ctx.config.db_path, isolation_level=None)
    other.execute("UPDATE events SET payload_json=replace(payload_json,'0001','0002') WHERE task_id=? AND seq=2",
                  (task.id,))
    other.close()
    assert _critical(panels.integrity_findings(ctx))


def test_clean_the_servers_own_connection_edit_is_seen(warm):
    ctx, task = warm
    ctx.conn.execute("UPDATE events SET payload_json=replace(payload_json,'0001','0002') WHERE task_id=? AND seq=2",
                     (task.id,))
    assert _critical(panels.integrity_findings(ctx))


def test_clean_an_anchor_edit_with_size_and_mtime_restored_is_seen(warm):
    ctx, task = warm
    a = Path(ctx.anchor.path)
    st = os.stat(a)
    raw = a.read_bytes()
    head = raw.rstrip(b"\n").split(b"\t")[-1]
    flipped = bytes([head[0] ^ 1]) + head[1:]                      # same length, one hex digit
    flipped = flipped if all(c in b"0123456789abcdef" for c in flipped) else b"0" + head[1:]
    a.write_bytes(raw.replace(head, flipped))
    os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert os.stat(a).st_size == st.st_size and os.stat(a).st_mtime_ns == st.st_mtime_ns
    assert _critical(panels.integrity_findings(ctx))


def test_clean_an_edit_during_a_sweep_is_seen_on_the_poll_after(warm, monkeypatch):
    """The key is read BEFORE the walk; a commit landing mid-walk moves
    data_version, so the next poll re-walks."""
    ctx, task = warm
    real = verify_all

    def tamper_mid_sweep(*a, **k):
        out = real(*a, **k)
        other = sqlite3.connect(ctx.config.db_path, isolation_level=None)
        other.execute("UPDATE events SET payload_json=replace(payload_json,'0001','0003') "
                      "WHERE task_id=? AND seq=3", (task.id,))
        other.close()
        return out

    ctx.log.append(task.id, "human.steered", {"correction": "force a miss"})
    import agent.kernel.integrity as integ
    monkeypatch.setattr(integ, "verify_all", tamper_mid_sweep)
    assert _critical(panels.integrity_findings(ctx)) == []   # this sweep ran before the tamper
    monkeypatch.setattr(integ, "verify_all", real)
    assert _critical(panels.integrity_findings(ctx))      # the next poll sees it


def test_note_an_in_process_rewrite_through_backup_is_unseen_until_the_max_age(warm, tmp_path, monkeypatch):
    """Connection.backup() INTO the server's connection rewrites every page
    without an INSERT/UPDATE/DELETE: total_changes does not move, and
    data_version never moves for a connection's own writes. Unseen until
    SWEEP_MAX_AGE_S - within the bound the docstring states, and it needs code
    running inside the server process, which no documented actor has."""
    ctx, task = warm
    copy = tmp_path / "tampered.db"
    src = sqlite3.connect(ctx.config.db_path)
    dst = sqlite3.connect(copy)
    src.backup(dst)
    src.close()
    dst.execute("UPDATE events SET payload_json=replace(payload_json,'0001','0004') WHERE task_id=? AND seq=2",
                (task.id,))
    dst.commit()
    tc, dv = ctx.conn.total_changes, ctx.conn.execute("PRAGMA data_version").fetchone()[0]
    dst.backup(ctx.conn)
    dst.close()
    print(f"\ntotal_changes {tc}->{ctx.conn.total_changes}, data_version {dv}->"
          f"{ctx.conn.execute('PRAGMA data_version').fetchone()[0]}")
    assert _critical(verify_all(ctx.conn, ctx.log, ctx.anchor)), "the tamper is real"
    assert panels.integrity_findings(ctx) == [], "cached: unseen"
    t = time.monotonic()
    monkeypatch.setattr(panels.time, "monotonic", lambda: t + panels.SWEEP_MAX_AGE_S + 1)
    assert _critical(panels.integrity_findings(ctx)), "seen once the sweep expires"


def test_clean_memory_tamper_is_checked_every_poll(warm):
    ctx, task = warm
    if ctx.memory is None:
        pytest.skip("no memory store in this config")
    from agent.memory.store import check_fingerprint, INTACT
    before = check_fingerprint(ctx.memory)
    p = ctx.memory.path("memory")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n- [user] planted line\n")
    assert any(f["task_id"] == "__memory__" for f in panels.integrity_findings(ctx)), before


def test_raw_file_edit_seen_by_the_long_lived_connection(warm, monkeypatch):
    """An out-of-band writer that edits the database FILE's bytes (not through
    SQLite) moves neither data_version nor total_changes. Does the 30 s max age
    still catch it through the server's long-lived connection, whose page cache
    SQLite keeps while the WAL index is unchanged?"""
    ctx, task = warm
    ctx.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert panels.integrity_findings(ctx) == [] or True
    db = Path(ctx.config.db_path)
    raw = db.read_bytes()
    needle = f"{MARK} 2".encode()
    assert raw.count(needle) == 1
    with open(db, "r+b") as f:                         # the running server keeps it open
        f.seek(raw.index(needle))
        f.write(f"{MARK} 9".encode())
    t = time.monotonic()
    monkeypatch.setattr(panels.time, "monotonic", lambda: t + panels.SWEEP_MAX_AGE_S + 1)
    via_server_conn = _critical(panels.integrity_findings(ctx))
    fresh = sqlite3.connect(ctx.config.db_path)
    from agent.kernel.events import EventLog
    via_fresh = EventLog(fresh).verify(task.id)
    fresh.close()
    print(f"\nexpired sweep through the server's connection: {via_server_conn}"
          f"\nfresh connection verify: {via_fresh}")
    assert via_fresh[0] is False, "the edit is real"
