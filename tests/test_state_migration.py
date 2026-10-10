from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agent import paths


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.delenv("BUCEPHALUS_DATA_DIR", raising=False)
    monkeypatch.delenv("BUCEPHALUS_CONFIG_DIR", raising=False)
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app, *a, **k: str(tmp_path / "data" / app))
    monkeypatch.setattr("platformdirs.user_config_dir", lambda app, *a, **k: str(tmp_path / "config" / app))
    return tmp_path


def _old(base: Path, kind: str) -> Path:
    d = base / kind / paths.LEGACY_APP
    (d / "memory").mkdir(parents=True)
    (d / "memory" / "MEMORY.md").write_text("- a fact\n", encoding="utf-8")
    (d / "checkouts" / "x-1234").mkdir(parents=True)
    (d / "checkouts" / "x-1234" / "agent.db").write_bytes(b"\x00db")
    return d


def test_the_old_data_dir_is_copied_once_on_first_use(dirs):
    old = _old(dirs, "data")
    new = paths.user_data_dir()
    assert new == dirs / "data" / paths.APP
    assert (new / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "- a fact\n"
    assert (new / "checkouts" / "x-1234" / "agent.db").read_bytes() == b"\x00db"
    assert (old / "memory" / "MEMORY.md").exists(), "the old directory is left as it was"
    (new / "memory" / "MEMORY.md").write_text("- changed\n", encoding="utf-8")
    paths.user_data_dir()
    assert (new / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "- changed\n"


def test_the_old_config_dir_is_copied_too(dirs):
    _old(dirs, "config")
    new = paths.user_config_dir()
    assert new == dirs / "config" / paths.APP and (new / "memory" / "MEMORY.md").exists()


def test_an_existing_new_dir_is_never_merged_into(dirs):
    _old(dirs, "data")
    new = dirs / "data" / paths.APP
    new.mkdir(parents=True)
    paths.user_data_dir()
    assert list(new.iterdir()) == []


def test_nothing_is_created_when_there_was_no_old_dir(dirs):
    paths.user_data_dir()
    assert not (dirs / "data").exists()


def test_an_explicit_dir_never_adopts_the_old_one(dirs, monkeypatch):
    _old(dirs, "data")
    monkeypatch.setenv("BUCEPHALUS_DATA_DIR", str(dirs / "explicit"))
    assert paths.user_data_dir() == dirs / "explicit"
    assert not (dirs / "data" / paths.APP).exists()


def test_a_failed_copy_leaves_nothing_half_made_and_says_so(dirs, monkeypatch):
    _old(dirs, "data")
    real = shutil.copytree

    def broken(src, dst, *a, **k):
        real(src, dst, *a, **k)
        raise OSError("disk full")

    monkeypatch.setattr(paths.shutil, "copytree", broken)
    with pytest.raises(RuntimeError, match="nothing was changed"):
        paths.user_data_dir()
    assert not (dirs / "data" / paths.APP).exists()
    assert not list((dirs / "data").glob("*migrating*"))
    monkeypatch.setattr(paths.shutil, "copytree", real)
    assert (paths.user_data_dir() / "memory" / "MEMORY.md").exists()


def test_a_concurrent_adopter_that_finishes_first_is_not_an_error(dirs, monkeypatch):
    _old(dirs, "data")
    new = dirs / "data" / paths.APP
    real = paths.os.replace

    def raced(src, dst):
        real(src, dst)
        raise OSError("another process got there first")

    monkeypatch.setattr(paths.os, "replace", raced)
    assert paths.user_data_dir() == new and (new / "memory" / "MEMORY.md").exists()


def test_each_process_stages_its_copy_apart(dirs, monkeypatch):
    _old(dirs, "data")
    seen = []
    real = paths.shutil.copytree
    monkeypatch.setattr(paths.shutil, "copytree", lambda s, d, *a, **k: (seen.append(Path(d).name), real(s, d, *a, **k))[1])
    paths.user_data_dir()
    assert seen and str(paths.os.getpid()) in seen[0]


def test_subprocesses_inherit_scratch_dirs_so_none_can_adopt_real_data():
    import os

    import platformdirs

    for var, fn in (("BUCEPHALUS_DATA_DIR", platformdirs.user_data_dir),
                    ("BUCEPHALUS_CONFIG_DIR", platformdirs.user_config_dir)):
        assert os.environ.get(var), f"{var} is not set: a subprocess would resolve the real directory"
