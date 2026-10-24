from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {"model", "quant", "ctx", "profile", "backend", "mechanisms", "settings", "tools", "edit_format", "versions",
          "hardware", "free_memory_gb"}


def test_the_card_names_everything_that_shapes_a_run(ctx):
    from agent.card import harness_card

    card = harness_card(ctx)
    assert FIELDS <= set(card)
    assert card["model"] == ctx.backends["cloud"].cfg.model and card["ctx"] == ctx.backends["cloud"].cfg.budgeted_window
    assert card["mechanisms"] == ctx.config.mechanisms.model_dump()
    assert card["tools"] == sorted(ctx.tools.names()) and card["edit_format"] == "edit_file"
    assert {"harness", "toolset", "code"} <= set(card["versions"]) and card["hardware"]["cpus"] >= 1
    assert "read_first" in card["backend"] and "long_files" in card["settings"]
    json.dumps(card)


def test_the_small_profile_card_says_so(small):
    from agent.card import harness_card

    card = harness_card(small)
    assert card["profile"] == "small" and card["edit_format"] == "hashline"


def test_the_users_own_model_server_is_never_asked(ctx, monkeypatch):
    import httpx

    from agent.card import harness_card

    ctx.backends["cloud"].cfg.model_server = "ollama"
    ctx.backends["cloud"].cfg.endpoint = "http://localhost:11434"
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("the user's server was asked"))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("the user's server was asked"))
    assert harness_card(ctx)["quant"] is None


@pytest.mark.parametrize("name", ["run.py", "families_run.py"])
def test_every_run_record_carries_the_card(name):
    text = (ROOT / "bench" / name).read_text(encoding="utf-8")
    assert "harness_card(" in text or "card_for(" in text


def test_the_eval_runner_records_carry_the_card():
    assert "harness_card(" in (ROOT / "src" / "agent" / "evals.py").read_text(encoding="utf-8")
