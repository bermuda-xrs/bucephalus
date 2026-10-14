from __future__ import annotations

from pathlib import Path

import yaml

from agent.config import config_from_raw
from agent.context import AgentContext

ROOT = Path(__file__).resolve().parents[1]


def _ctx(tmp_path):
    raw = yaml.safe_load((ROOT / "config.architect.yaml").read_text(encoding="utf-8"))
    raw.update(root=str(tmp_path), state_dir="")
    return AgentContext(config_from_raw(raw))


def test_the_architect_plans_and_reviews_and_the_editor_steps(tmp_path):
    ctx = _ctx(tmp_path)
    try:
        arch, editor = ctx.router.choose("plan"), ctx.router.choose("step")
        assert "8b" in arch.cfg.model and "3b" in editor.cfg.model
        assert ctx.router.choose("evaluate").cfg.model == arch.cfg.model
        assert editor.cfg.tool_profile == "small" and editor.cfg.evaluator_pass is True
    finally:
        ctx.close()


def test_the_evaluator_goes_through_the_router():
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(encoding="utf-8")
    body = src.split("def _evaluate(", 1)[1].split("\ndef ", 1)[0]
    assert 'router.choose("evaluate"' in body
