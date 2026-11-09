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
