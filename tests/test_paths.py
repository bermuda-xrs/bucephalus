"""State goes beside the source in a checkout, into user dirs once installed.

A wheel that writes `agent.db` into the current working directory behaves
differently depending on where you stand when you run it. That is the same
class of bug as `verify_program` resolving `artifact_scope` against the
process CWD, which produced a silent 0% on every task from the wrong
directory — so it gets the same treatment: decided in one place, and tested.

8.2 also requires re-verifying two path-sensitive guarantees AFTER the move:
the anchor must stay outside sandbox write scope, and no expected value may
be reachable from inside `artifact_scope`.
"""

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
    """Every existing run record was written against project-relative paths."""
    assert checkout_root(ROOT) is not None
    assert checkout_root(ROOT) == ROOT
    assert state_root(ROOT / "config.local.yaml") == ROOT


def test_an_installed_package_uses_user_dirs(tmp_path, monkeypatch):
    """No pyproject.toml/src marker anywhere above -> not a checkout."""
    monkeypatch.delenv("ALPHA_DATA_DIR", raising=False)
    lonely = tmp_path / "nowhere" / "deep"
    lonely.mkdir(parents=True)
    monkeypatch.chdir(lonely)
    assert checkout_root(lonely) is None
    assert state_root(lonely / "config.yaml") == user_data_dir()


def test_the_data_dir_override_wins(tmp_path, monkeypatch):
    """The post-install test must run from outside the checkout."""
    monkeypatch.setenv("ALPHA_DATA_DIR", str(tmp_path))
    assert state_root(ROOT / "config.local.yaml") == tmp_path


def test_state_dirs_are_created(tmp_path):
    made = ensure_state_dirs(tmp_path)
    assert {p.name for p in made} == set(STATE_DIRS)
    for d in STATE_DIRS:
        assert (tmp_path / d).is_dir()
    assert ensure_state_dirs(tmp_path) == []          # idempotent


def test_grading_is_state_not_package_data():
    """The grader holds expected values. It must never ship in a wheel."""
    assert "grading" in STATE_DIRS
    assert "skills_store" in STATE_DIRS
    assert "anchor" in STATE_DIRS


# --- 8.2 re-verification: both of these are path-sensitive ----------------


def test_the_anchor_is_still_outside_sandbox_write_scope(tmp_path):
    """Re-checked after the path move, as instructed.

    `reset_workspace` refuses to touch protected directories, and the sandbox
    confines writes to artifact_scope. The anchor holds the hash chain; a task
    that could write it could forge its own integrity.
    """
    from agent.workspace import UnsafeScope, reset_workspace

    with pytest.raises(UnsafeScope):
        reset_workspace(tmp_path, ["anchor"], {})
    with pytest.raises(UnsafeScope):
        reset_workspace(tmp_path, ["skills_store"], {})


def test_no_expected_value_in_scope_still_holds_after_the_move():
    """Re-run of the 0.2 guarantee, because the grader path just changed."""
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
