"""The capability gate, pinned where mutation testing found it unpinned
(iteration 3, 2.3; scripts/mutate.py on security/capabilities.py).

Each of these mutants - one member dropped from a protected list, one branch
flipped - left the gate's whole test set green. The redundant pairs (a name
in PROTECTED_FILES and in PROJECT_STATE) are recorded as equivalent in
docs/evidence/mutation-2026-09-28/README.md and are not re-tested here.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from agent.security.capabilities import (is_protected, render_preview, state_paths,
                                         within_scope)


@pytest.mark.parametrize("path", ["work/.env", "work/sub/config.yaml", "work/policy.md",
                                  "work/anchor/chain.anchor", "work/skills_store/s.md"])
def test_protected_names_are_protected_anywhere_even_inside_scope(path):
    """Kills each drop from PROTECTED_FILES / PROTECTED_DIRS: the name is
    protected wherever it appears, not only at the root."""
    assert is_protected(path, ".", ())


@pytest.mark.parametrize("name", ["cal.json", ".token_calibration.json", ".measurement.lock"])
def test_measurement_state_at_the_root_is_protected(ctx, name):
    """Kills the PROJECT_STATE drops nothing else covers."""
    assert is_protected(name, ctx.config.root, state_paths(ctx))


def test_the_rollback_journal_is_protected(ctx):
    """Kills DB_FILES dropping "-journal": a database mid-transaction in
    rollback mode holds the newest pages there."""
    db = ctx.config.resolve_state(ctx.config.db_path)
    assert is_protected(str(db) + "-journal", ctx.config.root, state_paths(ctx))


def test_a_sibling_configs_custom_anchor_and_skills_are_protected(ctx):
    """Kills _sibling_state dropping "anchor_path" or "skills_path": names
    nobody would guess are protected because a config names them."""
    root = Path(ctx.config.root)
    (root / "config.other.yaml").write_text(
        "anchor_path: ./vault/chain.other\nskills_path: ./kit\n", encoding="utf-8")
    state = state_paths(ctx)
    assert is_protected("vault/chain.other", str(root), state)
    assert is_protected("kit/skill.md", str(root), state)


def test_a_synced_checkouts_whole_state_dir_is_protected(ctx, tmp_path):
    """Kills `if cfg.state_dir:` negated: run records written there (1.1) are
    protected as a directory, not only the database and anchor by name."""
    ctx.config.state_dir = str(tmp_path / "state")
    target = tmp_path / "state" / "runs" / "promotions.jsonl"
    assert is_protected(str(target), ctx.config.root, state_paths(ctx))


def test_an_empty_scope_still_allows_a_call_with_no_path():
    """Kills `return True, ""` -> None / False: empty scope means no PATH is
    permitted, not that no tool is."""
    assert within_scope({"expression": "2+2"}, []) == (True, "")
    assert within_scope({"path": "work/a.txt"}, [])[0] is False


def test_the_approval_preview_is_truncated_not_reinterpreted():
    """Kills the render_preview / _short limits: what a human approves is
    bounded, and a long argument is visibly cut."""
    tool = types.SimpleNamespace(name="write_file")
    long = "x" * 500
    pv = render_preview(tool, {"path": "work/a.txt", "content": long})
    assert len(pv) <= 300
    assert "path='work/a.txt'" in pv
    shown = pv.split("content=", 1)[1].split(", path=", 1)[0]     # args are sorted
    assert shown.startswith("'xxx") and shown.endswith("...") and len(shown) == 80
    assert render_preview(tool, {"n": 1}) == "write_file(n=1)"


@pytest.mark.parametrize("key", ["path", "file", "filename", "dir", "directory", "dest",
                                 "src", "target"])
def test_every_path_shaped_argument_is_scope_checked(key):
    """Kills each _PATH_KEYS drop. Today's tools take only `path`; a future
    copy(src, dest) or move(file, target) is covered because these are."""
    ok, why = within_scope({key: "../outside.txt"}, ["work"], ".")
    assert not ok and key in why


def test_the_preview_stops_at_exactly_300_characters():
    """Kills the preview limit moved by one: many arguments, each short."""
    tool = types.SimpleNamespace(name="t")
    pv = render_preview(tool, {f"a{i:02d}": "v" * 30 for i in range(40)})
    assert len(pv) == 300
