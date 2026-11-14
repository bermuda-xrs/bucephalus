from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control import conversations
from agent.control.server import create_app
from agent.kernel.integrity import checkpoint, critical, verify_all
from agent.kernel.removal import TOMBSTONE
from agent.kernel.tasks import create_task, load_task, set_status
from agent.kernel.types import Budget, Taint, TaskContract, TaskStatus
from agent.ledger.artifacts import write_artifact
from test_console_reskin_guards import UI, _calls

SECRET = "ZEBRAQUARTZ-PRIVATE-GOAL"
PHRASE = "delete"


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def _fill(ctx, tid):
    ctx.log.append(tid, "model.called", {"purpose": "step", "step": 0})
    ctx.log.append(tid, "tool.intent", {"tool": "read_file", "step": 0, "key": f"key-{tid}", "preview": SECRET})
    ctx.log.append(tid, "tool.receipt", {"key": f"key-{tid}", "ok": True})
    write_artifact(ctx.conn, tid, "answer", f"answer about {SECRET}", Taint.TRUSTED, {"by": "test"})
    now = time.time()
    ctx.conn.execute("INSERT INTO receipts(idempotency_key, tool_name, args_hash, result_json, created_at) "
                     "VALUES (?,?,?,?,?)", (f"key-{tid}", "read_file", "h", f'"{SECRET}"', now))
    ctx.conn.execute("INSERT INTO plans(id, task_id, body_json, status, created_at) VALUES (?,?,?,?,?)",
                     ("p1", tid, f'{{"goal": "{SECRET}"}}', "approved", now))
    ctx.conn.execute("INSERT INTO approvals(id, task_id, tool, args_json, preview, reason, status, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (f"a-{tid}", tid, "write_file", "{}", SECRET, "r", "approved", now))
    ctx.conn.execute("INSERT INTO review_decisions(task_id, tool, arg_taint, verdict, rationale, backend, created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (tid, "write_file", "trusted", "approve", SECRET, "mock", now))
    ctx.conn.execute("INSERT INTO calls(task_id, backend, purpose, input_tokens, output_tokens, created_at) "
                     "VALUES (?,?,?,?,?,?)", (tid, "mock", "step", 10, 5, now))
    ctx.conn.commit()
    set_status(ctx.conn, ctx.log, load_task(ctx.conn, tid), TaskStatus.DONE)
    checkpoint(ctx.log, ctx.anchor, tid)


@pytest.fixture
def conv(ctx):
    c = conversations.create(ctx.conn, "Private work")
    ids = [conversations.add_message(ctx, c["id"], f"{SECRET} part {i}", ["work"], None, None)["task_id"]
           for i in range(2)]
    for t in ids:
        _fill(ctx, t)
    return c["id"], ids


def delete(api, cid, phrase=PHRASE):
    return api.post(f"/conversations/{cid}/delete", json={"phrase": phrase})


def _dump(conn) -> str:
    out = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table') AND name NOT LIKE 'sqlite_%'"):
        try:
            out += [repr(r) for r in conn.execute(f'SELECT * FROM "{name}"')]
        except sqlite3.OperationalError:
            continue
    return "\n".join(out)


def test_deleting_a_conversation_erases_every_trace_of_its_content(ctx, api, conv):
    cid, ids = conv
    assert SECRET in _dump(ctx.conn)
    r = delete(api, cid)
    assert r.status_code == 200, r.text
    assert sorted(r.json()["removed"]) == sorted(ids)
    assert SECRET not in _dump(ctx.conn)
    assert ctx.conn.execute("SELECT COUNT(*) FROM conversations WHERE id=?", (cid,)).fetchone()[0] == 0
    for t in ids:
        assert ctx.conn.execute("SELECT COUNT(*) FROM events WHERE task_id=?", (t,)).fetchone()[0] == 0


def test_the_text_is_gone_from_the_database_files_too(ctx, api, conv):
    delete(api, conv[0])
    db = Path(ctx.config.resolve_state(ctx.config.db_path))
    for f in (db, db.with_name(db.name + "-wal")):
        if f.exists():
            assert SECRET.encode() not in f.read_bytes(), f.name


def test_spend_and_trial_counts_stay(ctx, api, conv):
    before = ctx.conn.execute("SELECT COUNT(*), SUM(input_tokens) FROM calls").fetchone()
    delete(api, conv[0])
    assert ctx.conn.execute("SELECT COUNT(*), SUM(input_tokens) FROM calls").fetchone() == before


def test_verify_reports_a_removal_as_removed_not_as_tampering(ctx, api, conv):
    delete(api, conv[0])
    found = verify_all(ctx.conn, ctx.log, ctx.anchor)
    assert critical(found) == []
    removed = [f for f in found if "removed" in f["detail"]]
    assert len(removed) == 2 and all(f["severity"] == "info" for f in removed)
    assert all(t in ctx.anchor.all_tasks() for t in (TOMBSTONE + i for i in conv[1]))


def test_deleting_rows_by_hand_is_still_tampering(ctx, conv):
    _, ids = conv
    for t in ids:
        for table in ("events", "artifacts", "plans", "approvals", "conversation_tasks"):
            ctx.conn.execute(f"DELETE FROM {table} WHERE task_id=?", (t,))
        ctx.conn.execute("DELETE FROM tasks WHERE id=?", (t,))
    ctx.conn.commit()
    assert len(critical(verify_all(ctx.conn, ctx.log, ctx.anchor))) == 2


def test_a_forged_tombstone_row_without_the_anchor_line_is_still_tampering(ctx, conv):
    _, ids = conv
    t = ids[0]
    for table in ("events", "artifacts", "plans", "approvals", "conversation_tasks"):
        ctx.conn.execute(f"DELETE FROM {table} WHERE task_id=?", (t,))
    ctx.conn.execute("DELETE FROM tasks WHERE id=?", (t,))
    ctx.conn.execute("INSERT INTO task_tombstones(task_id, removed_at, head, events) VALUES (?,?,?,?)",
                     (t, time.time(), "0" * 64, 0))
    ctx.conn.commit()
    assert [f["task_id"] for f in critical(verify_all(ctx.conn, ctx.log, ctx.anchor))] == [t]


def test_a_delegated_task_goes_with_its_parent(ctx, api, conv):
    cid, ids = conv
    child = create_task(ctx.conn, ctx.log, TaskContract(goal=f"{SECRET} child", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=60), parent_id=ids[0]).id
    r = delete(api, cid)
    assert child in r.json()["removed"] and SECRET not in _dump(ctx.conn)


def test_a_running_task_is_not_deleted(ctx, api, conv):
    cid, ids = conv
    set_status(ctx.conn, ctx.log, load_task(ctx.conn, ids[1]), TaskStatus.RUNNING)
    assert delete(api, cid).status_code == 409
    assert SECRET in _dump(ctx.conn)


def test_a_task_whose_record_is_already_broken_is_not_deleted(ctx, api, conv):
    cid, ids = conv
    ctx.conn.execute("UPDATE events SET payload_json='{}' WHERE task_id=? AND seq=1", (ids[0],))
    ctx.conn.commit()
    r = delete(api, cid)
    assert r.status_code == 409 and "evidence" in r.json()["detail"]
    assert ctx.conn.execute("SELECT COUNT(*) FROM tasks WHERE id=?", (ids[0],)).fetchone()[0] == 1


def test_it_needs_the_typed_word_and_a_real_conversation(api, conv):
    assert delete(api, conv[0], "yes").status_code == 400
    assert delete(api, "c_nothing").status_code == 404


def test_the_task_checkpoints_on_disk_go_too(ctx, api, conv):
    cid, ids = conv
    shadow = Path(ctx.config.resolve_state("shadow"))
    for t in ids:
        (shadow / t).mkdir(parents=True)
        (shadow / t / "HEAD").write_text("ref", encoding="utf-8")
    keep = shadow / "t_someone_else"
    keep.mkdir()
    delete(api, cid)
    assert not any((shadow / t).exists() for t in ids) and keep.exists()


def test_the_page_offers_delete_through_its_route():
    assert ("POST", "/conversations/{}/delete") in _calls(UI)
    assert "deleteConversation" in UI
