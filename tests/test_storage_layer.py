"""The storage layer - every place that treats the main .db file as the database.

C41 was a test that scanned `agent.db` for absence while the rows it was
looking for sat in `agent.db-wal`. It bit twice in ten minutes. The sweep it
prompted covered every site that names a database path directly rather than
going through a connection, and found two more defects of the same family:

  * the agent's own database was readable as a FILE by any task whose scope
    covered the project root - a retrieval-isolation bypass
  * the sandbox copied a scoped database without its `-wal`, in both directions

Each test here was run against the code before the fix and goes red there.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from agent.execute import sandbox
from agent.kernel.db import snapshot
from agent.kernel.events import EventLog
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.security.capabilities import gate_tool_call


def _task(ctx, scope):
    return create_task(ctx.conn, ctx.log,
                       TaskContract(goal="mine", artifact_scope=scope),
                       Budget(usd=1.0, seconds=60))


def _tasks_in(path, task_id) -> int:
    c = sqlite3.connect(str(path))
    try:
        return c.execute("SELECT COUNT(*) FROM tasks WHERE id=?",
                         (task_id,)).fetchone()[0]
    except sqlite3.OperationalError:
        return 0          # the schema itself never reached the main file
    finally:
        c.close()


# -- the agent's own state is not a file the agent may read ----------------

@pytest.mark.parametrize("scope", [["."], []], ids=["root-scope", "no-scope"])
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_the_database_cannot_be_read_as_a_file(ctx, scope, suffix):
    """Under isolation, scope ["."]: both reads were allowed and the other
    arm's goal was readable. An empty scope allows every path, so it is the
    same hole with nothing declared."""
    ctx.config.isolate_retrieval = True
    me = _task(ctx, scope)
    rel = os.path.relpath(str(Path(ctx.config.db_path)) + suffix, ctx.config.root)
    d = gate_tool_call(ctx, me, ctx.tools.get("read_file"), {"path": rel})
    assert d.verdict == "deny", (
        f"read_file({rel!r}) reads every other task's goals and answers")


def test_an_anchor_moved_off_its_conventional_name_is_still_protected(ctx):
    """`anchor/` was protected by NAME. Point anchor_path elsewhere and the
    name protected an empty directory."""
    ctx.config.anchor_path = str(Path(ctx.config.root) / "elsewhere" / "chain.dat")
    me = _task(ctx, ["."])
    d = gate_tool_call(ctx, me, ctx.tools.get("write_file"),
                       {"path": "elsewhere/chain.dat", "content": "x"})
    assert d.verdict == "deny"


def test_ordinary_files_in_scope_are_unaffected(ctx):
    me = _task(ctx, ["."])
    d = gate_tool_call(ctx, me, ctx.tools.get("read_file"),
                       {"path": "work/notes.txt"})
    assert d.verdict == "allow"


# -- the sandbox carries a database whole -----------------------------------

@pytest.fixture
def copy_mode(monkeypatch):
    """This machine cannot always symlink, which is when the copy path runs."""
    def refuse(*a, **k):
        raise OSError("symlinks unavailable")
    monkeypatch.setattr(sandbox.os, "symlink", refuse)


def test_a_scoped_database_arrives_in_the_sandbox_with_its_newest_rows(
        tmp_path, copy_mode):
    src = tmp_path / "data.db"
    held = sqlite3.connect(str(src), isolation_level=None)
    held.execute("PRAGMA journal_mode=WAL")
    held.execute("PRAGMA wal_autocheckpoint=0")
    held.execute("CREATE TABLE t(v TEXT)")
    held.execute("INSERT INTO t VALUES ('in the wal')")
    try:
        assert Path(str(src) + "-wal").exists(), "precondition: a live database"
        entry = sandbox.link_into(str(tmp_path / "box"), str(src))
        assert entry["mode"] == "copy"
        c = sqlite3.connect(entry["dest"])
        try:
            rows = c.execute("SELECT v FROM t").fetchall()
        except sqlite3.OperationalError:
            rows = []
        c.close()
        assert rows == [("in the wal",)], "the sandbox saw a stale database"
    finally:
        held.close()


def test_rows_the_sandboxed_program_left_in_its_wal_come_back(tmp_path, copy_mode):
    src = tmp_path / "data.db"
    c = sqlite3.connect(str(src))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t(v TEXT)")
    c.commit()
    c.close()

    entry = sandbox.link_into(str(tmp_path / "box"), str(src))
    inside = sqlite3.connect(entry["dest"], isolation_level=None)
    inside.execute("PRAGMA wal_autocheckpoint=0")
    inside.execute("INSERT INTO t VALUES ('written in the sandbox')")
    try:
        sandbox._copy_back([entry])
    finally:
        inside.close()

    c = sqlite3.connect(str(src))
    assert c.execute("SELECT v FROM t").fetchall() == [("written in the sandbox",)]
    c.close()


def test_a_stale_sidecar_beside_the_original_is_removed_on_copy_back(
        tmp_path, copy_mode):
    """A main file copied back beside an OLDER `-wal` lets SQLite replay stale
    frames onto newer pages on the next open."""
    src = tmp_path / "data.bin"
    src.write_bytes(b"main")
    entry = sandbox.link_into(str(tmp_path / "box"), str(src))
    Path(str(src) + "-wal").write_bytes(b"frames left by an earlier crash")
    Path(entry["dest"]).write_bytes(b"main, changed")
    sandbox._copy_back([entry])
    assert not Path(str(src) + "-wal").exists()


# -- evidence ----------------------------------------------------------------

def test_a_snapshot_keeps_the_rows_a_file_copy_loses(ctx, task, tmp_path):
    db = Path(ctx.config.db_path)
    assert Path(str(db) + "-wal").exists(), "precondition: a live database"
    ev = tmp_path / "evidence"
    ev.mkdir()
    naive = ev / "copied.db"
    shutil.copy(db, naive)
    snap = snapshot(ctx.conn, ev / "snap.db")

    assert _tasks_in(snap, task.id) == 1
    assert _tasks_in(naive, task.id) == 0, (
        "precondition for the comparison: if a plain copy keeps the row, the "
        "WAL was checkpointed and this test no longer shows what it claims")
    c = sqlite3.connect(str(snap))
    assert EventLog(c).verify(task.id)[0] is True, "and the chain survives it"
    c.close()


def test_a_snapshot_refuses_to_overwrite_evidence(ctx, tmp_path):
    dest = tmp_path / "snap.db"
    dest.write_text("earlier evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        snapshot(ctx.conn, dest)
    assert dest.read_text(encoding="utf-8") == "earlier evidence"
