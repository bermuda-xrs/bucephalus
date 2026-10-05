from __future__ import annotations

from pathlib import Path

import pytest

from agent import paths
from agent.config import load_config
from agent.context import AgentContext
from agent.security.capabilities import is_protected, state_paths


def _checkout(parent: Path) -> Path:
    root = parent / "proj"
    (root / "src" / "agent").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    cfg = Path(__file__).resolve().parents[1] / "config.yaml"
    (root / "config.yaml").write_text(cfg.read_text(encoding="utf-8"), encoding="utf-8")
    return root


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHA_DATA_DIR", raising=False)
    monkeypatch.delenv("OneDrive", raising=False)
    d = tmp_path / "appdata"
    monkeypatch.setattr("platformdirs.user_data_dir", lambda *a, **k: str(d))
    return d


def _db_file(ctx) -> Path:
    return Path(ctx.conn.execute("PRAGMA database_list").fetchone()[2])


@pytest.mark.parametrize("sync", ["OneDrive", "OneDrive - Contoso", "Dropbox",
                                  "iCloudDrive", "Google Drive"])
def test_a_checkout_in_a_synced_folder_keeps_its_state_outside_it(tmp_path, appdata,
                                                                  capsys, sync):
    root = _checkout(tmp_path / sync / "Desktop")
    cfg = load_config(root / "config.yaml")
    ctx = AgentContext(cfg)
    try:
        db, anchor = _db_file(ctx), Path(ctx.anchor.path)
        for p in (db, anchor):
            assert tmp_path / sync not in p.parents, f"{p} is inside {sync}"
            assert appdata in p.parents
        assert Path(cfg.root) == root
        for p in (db, anchor, Path(str(db) + "-wal")):
            assert is_protected(str(p), cfg.root, state_paths(ctx)), p
    finally:
        ctx.close()
    assert "synced folder" in capsys.readouterr().err


def test_the_notice_is_given_once(tmp_path, appdata, capsys):
    root = _checkout(tmp_path / "OneDrive")
    load_config(root / "config.yaml")
    capsys.readouterr()
    load_config(root / "config.yaml")
    assert "synced folder" not in capsys.readouterr().err


def test_the_onedrive_environment_variable_names_a_sync_root(tmp_path, appdata,
                                                             monkeypatch):
    od = tmp_path / "Personal Cloud"
    monkeypatch.setenv("OneDrive", str(od))
    assert paths.sync_root(od / "a" / "b") == od.resolve()


def test_an_ordinary_checkout_is_unchanged(tmp_path, appdata):
    root = _checkout(tmp_path / "code")
    cfg = load_config(root / "config.yaml")
    ctx = AgentContext(cfg)
    try:
        assert _db_file(ctx).parent == root
        assert not (appdata / "checkouts").exists()
    finally:
        ctx.close()


@pytest.mark.parametrize("name", ["Box", "Nextcloud", "ownCloud", "MEGA", "MEGAsync",
                                  "Proton Drive", "Sync", "SynologyDrive", "Tresorit"])
def test_D76_more_sync_clients_by_folder_name(tmp_path, monkeypatch, name):
    from agent.paths import sync_root

    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    checkout = tmp_path / name / "projects" / "alpha"
    checkout.mkdir(parents=True)
    assert sync_root(checkout) == (tmp_path / name).resolve()


@pytest.mark.parametrize("marker", [".dropbox", ".nextcloudsync.log", ".owncloudsync.log",
                                    ".sync_a1b2c3.db", ".SynologyWorkingDirectory", ".debris"])
def test_D76_sync_clients_by_the_marker_they_leave(tmp_path, monkeypatch, marker):
    from agent.paths import sync_root

    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    synced = tmp_path / "Documents"
    (synced / "alpha").mkdir(parents=True)
    (synced / marker).write_text("", encoding="utf-8")
    assert sync_root(synced / "alpha") == synced.resolve()


def test_D76_an_ordinary_folder_is_not_synced(tmp_path, monkeypatch):
    from agent.paths import sync_root

    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "work" / "alpha").mkdir(parents=True)
    assert sync_root(tmp_path / "work" / "alpha") is None
