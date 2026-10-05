from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.security.capabilities import gate_tool_call, is_protected, within_scope

windows = pytest.mark.skipif(sys.platform != "win32", reason="NTFS data streams")


def _client(ctx):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


@windows
@pytest.mark.parametrize("name", [".env::$DATA", ".ENV::$data", "policy.md:hidden",
                                  "config.yaml::$DATA", "work/notes.txt:ads"])
def test_S1_a_data_stream_spelling_is_refused_everywhere(ctx, workspace, name):
    root = Path(ctx.config.root)
    task = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["."]),
                       Budget(usd=1.0, seconds=60))
    before = {p.name for p in root.iterdir()}
    r = _client(ctx).post(f"/tasks/{task.id}/files",
                          json={"path": name, "content_b64": base64.b64encode(b"ALPHA_TOKEN=x").decode()})
    assert r.status_code == 403, (name, r.status_code, r.text)
    assert {p.name for p in root.iterdir()} == before, "a file appeared"
    assert is_protected(name, str(root), ())
    assert within_scope({"path": name}, ["."], str(root))[0] is False
    d = gate_tool_call(ctx, task, ctx.tools.get("write_file"), {"path": name, "content": "x"})
    assert d.verdict == "deny"


def test_S2_the_tui_never_decides_an_approval_it_is_not_showing(ctx, workspace):
    from agent.tui.app import AlphaTUI

    other = create_task(ctx.conn, ctx.log, TaskContract(goal="other", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=60))
    aid = ctx.request_approval(other, ctx.tools.get("write_file"),
                               {"path": "work/report.md", "content": "x"}, "needs a human", 0)
    shown = create_task(ctx.conn, ctx.log, TaskContract(goal="shown", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=60))

    async def go():
        app = AlphaTUI(ctx, task_id=shown.id)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            app.query_one("#trail").focus()
            await pilot.press("a")
            await pilot.pause(1.0)
    asyncio.run(go())
    status = ctx.conn.execute("SELECT status FROM approvals WHERE id=?", (aid,)).fetchone()[0]
    assert status == "pending", "allow-once decided another task's approval"
    assert not [e for e in ctx.log.events(other.id) if e["type"] == "model.called"]


def test_S2_resuming_from_the_tui_needs_a_signed_plan_when_plans_are_required(ctx, workspace):
    from agent.tui.app import AlphaTUI

    ctx.config.autonomy.plan_required = True
    t = create_task(ctx.conn, ctx.log, TaskContract(goal="t", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=60))
    ctx.request_approval(t, ctx.tools.get("write_file"),
                         {"path": "work/a.txt", "content": "x"}, "needs a human", 0)

    async def go():
        app = AlphaTUI(ctx, task_id=t.id)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            app.query_one("#trail").focus()
            await pilot.press("a")
            await pilot.pause(1.5)
    asyncio.run(go())
    assert not [e for e in ctx.log.events(t.id) if e["type"] == "model.called"], \
        "a task with no signed plan ran from the TUI"


def test_S4_a_checkouts_moved_state_is_protected_under_the_data_root(ctx, workspace):
    from agent.security.capabilities import state_paths

    root = Path(ctx.config.root)
    db = root / "checkouts" / "proj-1234abcd" / "agent.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"SQLite format 3\x00")
    assert is_protected("checkouts/proj-1234abcd/agent.db", str(root), state_paths(ctx))
    task = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["."]),
                       Budget(usd=1.0, seconds=60))
    d = gate_tool_call(ctx, task, ctx.tools.get("read_file"), {"path": "checkouts/proj-1234abcd/agent.db"})
    assert d.verdict == "deny"


def test_I2_a_walk_computes_the_protected_set_once(ctx, workspace, monkeypatch):
    import agent.security.capabilities as caps

    for i in range(30):
        (workspace / f"f{i}.txt").write_text("needle\n", encoding="utf-8")
    task = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=60))
    calls = []
    real = caps.task_state_paths
    monkeypatch.setattr(caps, "task_state_paths", lambda *a: calls.append(1) or real(*a))
    hits = ctx.tools.get("grep").fn(ctx, task, pattern="needle", path="work")["matches"]
    assert len(hits) == 30
    assert len(calls) <= 2, f"task_state_paths ran {len(calls)} times for one grep"
    calls.clear()
    from agent.control import files
    monkeypatch.setattr(files, "task_state_paths", lambda *a: calls.append(1) or real(*a))
    assert len(files.browse(ctx, task, "work")["entries"]) == 30
    assert len(calls) <= 2, f"task_state_paths ran {len(calls)} times for one listing"


def test_I1_a_sweep_reads_the_anchor_once_not_per_task(ctx, workspace, monkeypatch):
    from agent.kernel.integrity import checkpoint, verify_all

    for _ in range(5):
        t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=60))
        checkpoint(ctx.log, ctx.anchor, t.id)
    reads = []
    real = Path.read_text

    def counting(self, *a, **k):
        if self == ctx.anchor.path:
            reads.append(1)
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", counting)
    assert verify_all(ctx.conn, ctx.log, ctx.anchor) == []
    assert len(reads) <= 2, f"{len(reads)} reads of the anchor for one sweep of 5 tasks"


def _alerts(ctx):
    return _client(ctx).get("/alerts").json()["integrity"]


def _walks(monkeypatch):
    from agent.kernel.events import EventLog

    n = [0]
    real = EventLog.verify

    def counting(self, task_id):
        n[0] += 1
        return real(self, task_id)
    monkeypatch.setattr(EventLog, "verify", counting)
    return n


def _a_task_with_events(ctx):
    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=60))
    for i in range(3):
        ctx.log.append(t.id, "tool.receipt", {"tool": "read_file", "key": f"k{i}", "ok": True})
    return t


def test_D72_an_unchanged_database_is_not_rewalked(ctx, workspace, monkeypatch):
    _a_task_with_events(ctx)
    walks = _walks(monkeypatch)
    assert _alerts(ctx) == []
    first = walks[0]
    assert first > 0
    assert _alerts(ctx) == [] and _alerts(ctx) == []
    assert walks[0] == first, f"{walks[0] - first} chain walks for two unchanged polls"


def test_D72_an_edit_by_another_connection_is_seen_on_the_next_poll(ctx, workspace):
    import sqlite3

    t = _a_task_with_events(ctx)
    assert _alerts(ctx) == []
    other = sqlite3.connect(ctx.config.resolve_state(ctx.config.db_path))
    other.execute("UPDATE events SET payload_json=? WHERE task_id=? AND seq=1",
                  ('{"tool": "write_file", "key": "k0", "ok": true}', t.id))
    other.commit()
    other.close()
    found = [f for f in _alerts(ctx) if f.get("task_id") == t.id]
    assert found and found[0]["severity"] == "critical", found


def test_D72_an_anchor_change_is_seen_on_the_next_poll(ctx, workspace):
    t = _a_task_with_events(ctx)
    assert _alerts(ctx) == []
    ctx.anchor.record(t.id, "f" * 64)
    found = [f for f in _alerts(ctx) if f.get("task_id") == t.id]
    assert found and found[0]["severity"] == "critical", found


def test_D72_an_anchor_edited_in_place_with_its_mtime_restored_is_seen(ctx, workspace):
    import os

    from agent.kernel.integrity import checkpoint

    t = _a_task_with_events(ctx)
    checkpoint(ctx.log, ctx.anchor, t.id)
    assert _alerts(ctx) == []
    path = ctx.anchor.path
    st = os.stat(path)
    raw = path.read_bytes()
    head = ctx.log.head(t.id).encode()
    assert head in raw
    path.write_bytes(raw.replace(head, b"f" * len(head)))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    found = [f for f in _alerts(ctx) if f.get("task_id") == t.id]
    assert found and found[0]["severity"] == "critical", found


def test_D72_a_write_through_the_servers_own_connection_is_seen(ctx, workspace):
    t = _a_task_with_events(ctx)
    assert _alerts(ctx) == []
    ctx.conn.execute("UPDATE events SET payload_json=? WHERE task_id=? AND seq=1",
                     ('{"tool": "write_file", "key": "k0", "ok": true}', t.id))
    found = [f for f in _alerts(ctx) if f.get("task_id") == t.id]
    assert found and found[0]["severity"] == "critical", found


def test_D72_a_cached_sweep_expires(ctx, workspace, monkeypatch):
    from agent.control import panels

    _a_task_with_events(ctx)
    walks = _walks(monkeypatch)
    _alerts(ctx)
    first = walks[0]
    real = panels.time.monotonic
    monkeypatch.setattr(panels.time, "monotonic", lambda: real() + panels.SWEEP_MAX_AGE_S + 1)
    _alerts(ctx)
    assert walks[0] > first, "a cached sweep outlived its maximum age"
