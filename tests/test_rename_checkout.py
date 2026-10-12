from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rename_checkout.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"), reason="Windows PowerShell")


def _checkout(tmp_path, name="alpha-test-1"):
    root = tmp_path / "Βουκεφάλας" / name
    (root / "src" / "agent").mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    return root


def _state(data: Path, root: Path) -> Path:
    import hashlib

    r = root.resolve()
    return data / "checkouts" / f"{r.name}-{hashlib.sha256(str(r).encode('utf-8')).hexdigest()[:8]}"


def _run(root, data, *extra):
    env = {k: v for k, v in os.environ.items() if k not in ("BUCEPHALUS_SCRATCH_ONLY",)}
    env["BUCEPHALUS_DATA_DIR"] = str(data)
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),  # noqa: S603,S607
                           "-Root", str(root), *extra], capture_output=True, text=True, env=env, timeout=120)


def test_it_renames_the_folder_and_moves_its_state_directory(tmp_path):
    root, data = _checkout(tmp_path), tmp_path / "data"
    old_state = _state(data, root)
    (old_state / "runs").mkdir(parents=True)
    (old_state / "agent.db").write_text("db", encoding="utf-8")
    r = _run(root, data)
    assert r.returncode == 0, r.stdout + r.stderr
    new = root.parent / "bucephalus"
    assert new.is_dir() and not root.exists()
    assert (_state(data, new) / "agent.db").read_text(encoding="utf-8") == "db" and not old_state.exists()


def test_what_if_changes_nothing(tmp_path):
    root, data = _checkout(tmp_path), tmp_path / "data"
    (_state(data, root)).mkdir(parents=True)
    r = _run(root, data, "-WhatIf")
    assert r.returncode == 0 and root.exists() and _state(data, root).exists() and "would" in r.stdout.lower()


def test_it_refuses_when_the_target_exists_or_the_folder_is_not_a_checkout(tmp_path):
    root, data = _checkout(tmp_path), tmp_path / "data"
    (root.parent / "bucephalus").mkdir()
    r = _run(root, data)
    assert r.returncode != 0 and root.exists() and "exists" in (r.stdout + r.stderr)
    plain = tmp_path / "plain"
    plain.mkdir()
    r = _run(plain, data)
    assert r.returncode != 0 and plain.exists()


def test_a_checkout_with_no_state_directory_is_still_renamed(tmp_path):
    root, data = _checkout(tmp_path), tmp_path / "data"
    r = _run(root, data)
    assert r.returncode == 0 and (root.parent / "bucephalus").is_dir()
