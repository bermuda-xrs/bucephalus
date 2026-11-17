from __future__ import annotations

import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from agent.control import system
from agent.control.server import create_app
from test_console_reskin_guards import HTML, UI, _calls


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_the_system_page_reports_the_host_and_the_record(api):
    d = api.get("/system").json()
    from agent import HARNESS_VERSION

    assert d["harness_version"] == HARNESS_VERSION and d["cpus"] >= 1 and d["disk_free"] > 0
    assert d["db_bytes"] > 0 and d["uptime_s"] >= 0 and d["os"] and d["python"]
    assert d["restore"].startswith("bucephalus"), "restore is a command, never a button"


def test_a_backup_is_a_zip_whose_database_opens_and_holds_the_record(ctx, api, task, tmp_path):
    r = api.post("/system/backup")
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    got = api.get("/system/backups").json()["backups"]
    assert [b["name"] for b in got] == [name]
    out = tmp_path / "x"
    with zipfile.ZipFile(system.backups_dir() / name) as z:
        assert {"agent.db", "chain.anchor"} <= set(z.namelist())
        z.extractall(out)
    assert sqlite3.connect(out / "agent.db").execute("SELECT COUNT(*) FROM tasks WHERE id=?", (task.id,)).fetchone()[0] == 1
    assert api.get(f"/system/backups/{name}").content == (system.backups_dir() / name).read_bytes()


@pytest.mark.parametrize("name", ["%2e%2e", "..%2fx.zip", "nothing.zip", "agent.db"])
def test_only_a_backup_in_the_list_can_be_downloaded(api, name):
    assert api.get(f"/system/backups/{name}").status_code == 404


def test_a_backup_inside_a_synced_folder_is_refused(api, monkeypatch, tmp_path):
    monkeypatch.setattr(system, "sync_root", lambda p: tmp_path)
    r = api.post("/system/backup")
    assert r.status_code == 409 and "sync" in r.json()["detail"].lower()
    assert not (system.backups_dir()).exists() or not list(system.backups_dir().glob("*.zip"))


def test_a_backup_is_allowed_while_a_task_runs(ctx, api, task):
    from agent.kernel.tasks import set_status
    from agent.kernel.types import TaskStatus

    set_status(ctx.conn, ctx.log, task, TaskStatus.RUNNING)
    assert api.post("/system/backup").status_code == 200


def test_the_page_reads_its_routes_and_offers_no_restore_or_update():
    assert {("GET", "/system"), ("POST", "/system/backup"), ("GET", "/system/backups")} <= _calls(UI)
    assert 'data-pane="system"' in HTML
    block = UI[UI.index("async system()"):]
    block = block[:block.index("\n  async ")]
    for word in ("restart", "update now", "restore from", "debug share"):
        assert word not in block.lower().replace("restore:", ""), word


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX modes")
def test_a_backup_and_its_folder_are_owner_only(api):
    name = api.post("/system/backup").json()["name"]
    assert (system.backups_dir().stat().st_mode & 0o777) == 0o700
    assert ((system.backups_dir() / name).stat().st_mode & 0o777) == 0o600


def _backup(api):
    return system.backups_dir() / api.post("/system/backup").json()["name"]


def _rezip(src, dest, edit):
    import shutil
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        edit(Path(tmp))
        with zipfile.ZipFile(dest, "w") as z:
            for f in sorted(Path(tmp).rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(tmp).as_posix())
    shutil.copystat(src, dest)
    return dest


def _tamper(d):
    c = sqlite3.connect(d / "agent.db")
    c.execute("UPDATE events SET payload_json='{}' WHERE seq=0")
    c.commit()
    c.close()


def test_checking_a_backup_verifies_its_record_without_touching_the_live_one(ctx, api, task):
    from agent.kernel.integrity import checkpoint

    checkpoint(ctx.log, ctx.anchor, task.id)
    name = _backup(api).name
    before = ctx.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    r = api.post(f"/system/backups/{name}/check")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["tasks"] == 1 and d["events"] >= 1 and d["problems"] == []
    assert ctx.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before


def test_a_backup_whose_record_was_changed_fails_the_check(ctx, api, task):
    from agent.kernel.integrity import checkpoint

    checkpoint(ctx.log, ctx.anchor, task.id)
    good = _backup(api)
    bad = _rezip(good, good.with_name("bucephalus-20000101-000000.zip"), _tamper)
    d = api.post(f"/system/backups/{bad.name}/check").json()
    assert d["ok"] is False and d["problems"]


def test_a_zip_without_a_database_fails_the_check(api, task):
    good = _backup(api)
    bad = _rezip(good, good.with_name("bucephalus-20000101-000001.zip"), lambda d: (d / "agent.db").unlink())
    d = api.post(f"/system/backups/{bad.name}/check").json()
    assert d["ok"] is False and "agent.db" in d["problems"][0]


def test_only_a_backup_in_the_list_can_be_checked(api):
    assert api.post("/system/backups/nothing.zip/check").status_code == 404


def test_restore_puts_the_backup_in_place_and_keeps_what_it_replaced(ctx, api, task):
    from agent.kernel.integrity import checkpoint
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    checkpoint(ctx.log, ctx.anchor, task.id)
    zip_path = _backup(api)
    later = create_task(ctx.conn, ctx.log, TaskContract(goal="after the backup", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=60)).id
    db = ctx.config.resolve_state(ctx.config.db_path)
    out = system.restore(ctx, zip_path)
    c = sqlite3.connect(db)
    assert [r[0] for r in c.execute("SELECT id FROM tasks")] == [task.id]
    c.close()
    kept = sqlite3.connect(out["replaced"] + "/" + db.name)
    assert later in [r[0] for r in kept.execute("SELECT id FROM tasks")]
    kept.close()


def test_restore_refuses_a_backup_that_fails_the_check_and_changes_nothing(ctx, api, task):
    from agent.kernel.integrity import checkpoint

    checkpoint(ctx.log, ctx.anchor, task.id)
    good = _backup(api)
    bad = _rezip(good, good.with_name("bucephalus-20000101-000000.zip"), _tamper)
    with pytest.raises(system.Unsound):
        system.restore(ctx, bad)
    assert ctx.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_restore_refuses_while_the_console_is_running(ctx, api, task):
    from agent.kernel.integrity import checkpoint
    from agent.runlock import RunLockBusy

    checkpoint(ctx.log, ctx.anchor, task.id)
    zip_path = _backup(api)
    held = system.console_lock(ctx).acquire()
    try:
        with pytest.raises(RunLockBusy):
            system.restore(ctx, zip_path)
    finally:
        held.release()
    assert ctx.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_serving_the_console_holds_the_lock_restore_needs():
    import inspect

    from agent import cli, desktop
    from agent.control import server

    for start in (cli.cmd_serve, desktop.start_server, server.serve):
        assert "hold_console_lock" in inspect.getsource(start), start.__name__


def test_the_system_page_says_when_the_code_on_disk_is_newer_than_the_running_console(ctx, api, monkeypatch):
    assert api.get("/system").json()["code_changed"] is False
    monkeypatch.setattr(system, "STARTED", 0.0)
    assert api.get("/system").json()["code_changed"] is True
    assert "code_changed" in UI


def test_the_page_offers_the_check_through_its_route():
    assert ("POST", "/system/backups/{}/check") in _calls(UI) and "checkBackup" in UI
