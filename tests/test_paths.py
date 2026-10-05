from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent.paths import (
    STATE_DIRS,
    checkout_root,
    ensure_state_dirs,
    state_root,
    user_data_dir,
)

ROOT = Path(__file__).resolve().parents[1]


def test_a_checkout_keeps_state_beside_the_source():
    assert checkout_root(ROOT) is not None
    assert checkout_root(ROOT) == ROOT
    assert state_root(ROOT / "config.local.yaml") == ROOT


def test_an_installed_package_uses_user_dirs(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHA_DATA_DIR", raising=False)
    lonely = tmp_path / "nowhere" / "deep"
    lonely.mkdir(parents=True)
    monkeypatch.chdir(lonely)
    assert checkout_root(lonely) is None
    assert state_root(lonely / "config.yaml") == user_data_dir()


def test_the_data_dir_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_DATA_DIR", str(tmp_path))
    assert state_root(ROOT / "config.local.yaml") == tmp_path


def test_state_dirs_are_created(tmp_path):
    made = ensure_state_dirs(tmp_path)
    assert {p.name for p in made} == set(STATE_DIRS)
    for d in STATE_DIRS:
        assert (tmp_path / d).is_dir()
    assert ensure_state_dirs(tmp_path) == []


def test_grading_is_state_not_package_data():
    assert "grading" in STATE_DIRS
    assert "skills_store" in STATE_DIRS
    assert "anchor" in STATE_DIRS


def test_the_anchor_is_still_outside_sandbox_write_scope(tmp_path):
    from agent.workspace import UnsafeScope, reset_workspace

    with pytest.raises(UnsafeScope):
        reset_workspace(tmp_path, ["anchor"], {})
    with pytest.raises(UnsafeScope):
        reset_workspace(tmp_path, ["skills_store"], {})


def test_no_expected_value_in_scope_still_holds_after_the_move():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "aero_paths_check", ROOT / "bench" / "suites" / "archive" / "aero_calc.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    bad = []
    for st in mod.as_suite_tasks():
        scopes = [s.rstrip("/") for s in st.artifact_scope]
        for path, content in st.fixtures.items():
            if any(path == s or path.startswith(s + "/") for s in scopes):
                if "EXPECTED" in content:
                    bad.append(f"{st.task_key}: {path}")
    assert not bad, f"expected values reachable inside scope after the move: {bad}"
