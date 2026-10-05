from __future__ import annotations

import types
from pathlib import Path

import pytest

from agent.security.capabilities import (is_protected, render_preview, state_paths,
                                         within_scope)


@pytest.mark.parametrize("path", ["work/.env", "work/sub/config.yaml", "work/policy.md",
                                  "work/anchor/chain.anchor", "work/skills_store/s.md"])
def test_protected_names_are_protected_anywhere_even_inside_scope(path):
    assert is_protected(path, ".", ())


@pytest.mark.parametrize("name", ["cal.json", ".token_calibration.json", ".measurement.lock"])
def test_measurement_state_at_the_root_is_protected(ctx, name):
    assert is_protected(name, ctx.config.root, state_paths(ctx))


def test_the_rollback_journal_is_protected(ctx):
    db = ctx.config.resolve_state(ctx.config.db_path)
    assert is_protected(str(db) + "-journal", ctx.config.root, state_paths(ctx))


def test_a_sibling_configs_custom_anchor_and_skills_are_protected(ctx):
    root = Path(ctx.config.root)
    (root / "config.other.yaml").write_text(
        "anchor_path: ./vault/chain.other\nskills_path: ./kit\n", encoding="utf-8")
    state = state_paths(ctx)
    assert is_protected("vault/chain.other", str(root), state)
    assert is_protected("kit/skill.md", str(root), state)


def test_a_synced_checkouts_whole_state_dir_is_protected(ctx, tmp_path):
    ctx.config.state_dir = str(tmp_path / "state")
    target = tmp_path / "state" / "runs" / "promotions.jsonl"
    assert is_protected(str(target), ctx.config.root, state_paths(ctx))


def test_an_empty_scope_still_allows_a_call_with_no_path():
    assert within_scope({"expression": "2+2"}, []) == (True, "")
    assert within_scope({"path": "work/a.txt"}, [])[0] is False


def test_the_approval_preview_is_truncated_not_reinterpreted():
    tool = types.SimpleNamespace(name="write_file")
    long = "x" * 500
    pv = render_preview(tool, {"path": "work/a.txt", "content": long})
    assert len(pv) <= 300
    assert "path='work/a.txt'" in pv
    shown = pv.split("content=", 1)[1].split(", path=", 1)[0]
    assert shown.startswith("'xxx") and shown.endswith("...") and len(shown) == 80
    assert render_preview(tool, {"n": 1}) == "write_file(n=1)"


@pytest.mark.parametrize("key", ["path", "file", "filename", "dir", "directory", "dest",
                                 "src", "target"])
def test_every_path_shaped_argument_is_scope_checked(key):
    ok, why = within_scope({key: "../outside.txt"}, ["work"], ".")
    assert not ok and key in why


def test_the_preview_stops_at_exactly_300_characters():
    tool = types.SimpleNamespace(name="t")
    pv = render_preview(tool, {f"a{i:02d}": "v" * 30 for i in range(40)})
    assert len(pv) == 300


def test_a_tool_without_a_readable_signature_keeps_its_arguments():
    from agent.security.capabilities import _effective_args

    class NoSig:
        @property
        def __signature__(self):
            raise ValueError("no signature")

        def __call__(self, **kw):
            return kw

    tool = types.SimpleNamespace(name="t", fn=NoSig())
    assert _effective_args(tool, {"path": "../x"}) == {"path": "../x"}
