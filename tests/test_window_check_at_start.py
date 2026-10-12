from __future__ import annotations

import json

import pytest

from agent.execute.loop import run_task

FINAL = '<final>{"answer": "ok"}</final>'


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


@pytest.fixture
def served(ctx, monkeypatch):
    from agent.models import local

    seen = []

    def go(context_length, up=True):
        def status(endpoint):
            seen.append(endpoint)
            return {"up": up, "managed": True, "loaded": [{"name": "mock-cloud", "context_length": context_length}] if up else []}

        monkeypatch.setattr(local, "status", status)
        cfg = ctx.backends["cloud"].cfg
        cfg.model_server, cfg.endpoint, cfg.usable_context_window = "ollama", "http://localhost:11435", 16384
        return seen

    return go


def test_a_server_holding_less_context_than_the_config_budgets_is_recorded(ctx, task, served):
    served(4096)
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, task.id)
    got = _events(ctx, task, "model.window_short")
    assert got and got[0]["window"] == {"server": 4096, "configured": 16384} and "OLLAMA_CONTEXT_LENGTH" in got[0]["warning"]


def test_enough_context_records_nothing(ctx, task, served):
    served(16384)
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, task.id)
    assert not _events(ctx, task, "model.window_short")


def test_a_server_that_is_down_records_nothing_and_the_task_still_runs(ctx, task, served):
    served(None, up=False)
    ctx.backends["cloud"].queue(FINAL)
    assert run_task(ctx, task.id).status.startswith("done") and not _events(ctx, task, "model.window_short")


def test_a_backend_with_no_model_server_is_never_asked(ctx, task, monkeypatch):
    from agent.models import local

    monkeypatch.setattr(local, "status", lambda e: pytest.fail("asked a server that is not declared"))
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, task.id)
