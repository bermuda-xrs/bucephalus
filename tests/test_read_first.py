from __future__ import annotations

import json

from agent.config import BackendConfig
from agent.execute.loop import run_task
from agent.execute.schema import build_action_schema
from agent.execute.tools import default_registry
from agent.kernel.tasks import create_task

FINAL = '<final>{"answer": "ok"}</final>'
JFINAL = '{"kind": "final", "answer": "ok"}'
LIST = '<action tool="read_file">{"path": "work/a.txt"}</action>'


def _kinds(schema):
    return [b["properties"]["kind"]["const"] for b in schema["anyOf"]]


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


def test_it_is_on_for_the_small_profile_only_unless_declared():
    assert BackendConfig(kind="mock", model="m").requires_observation is False
    assert BackendConfig(kind="mock", model="m", tool_profile="patch").requires_observation is False
    assert BackendConfig(kind="mock", model="m", tool_profile="small").requires_observation is True
    assert BackendConfig(kind="mock", model="m", tool_profile="small", read_first=False).requires_observation is False
    assert BackendConfig(kind="mock", model="m", read_first=True).requires_observation is True


def test_the_schema_can_leave_out_the_final_branch():
    assert "final" in _kinds(build_action_schema(default_registry(), False))
    assert "final" not in _kinds(build_action_schema(default_registry(), False, allow_final=False))


def _ollama(c):
    c.backends["cloud"].cfg.trust_tier = "local"
    c.backends["cloud"].cfg.model_server = "ollama"


def test_no_final_branch_before_the_first_observation_then_it_returns(small, task, workspace):
    (workspace / "a.txt").write_text("hi", encoding="utf-8")
    _ollama(small)
    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue('{"kind": "action", "tool": "read_file", "args": {"path": "work/a.txt"}}', JFINAL)
    assert run_task(small, t.id).status.startswith("done")
    calls = small.backends["cloud"].calls
    assert "final" not in _kinds(calls[0]["schema"]) and "final" in _kinds(calls[1]["schema"])


def test_an_early_answer_is_held_back_where_there_is_no_schema(small, task, workspace):
    (workspace / "a.txt").write_text("hi", encoding="utf-8")
    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue(FINAL, LIST, FINAL)
    assert run_task(small, t.id).status.startswith("done")
    calls = small.backends["cloud"].calls
    assert len(calls) == 3 and "look at something first" in calls[1]["tail"]
    assert len(_events(small, t, "read_first.held")) == 1


def test_the_full_profile_answers_at_once(ctx, task):
    ctx.backends["cloud"].queue(FINAL)
    assert run_task(ctx, task.id).status.startswith("done")
    assert len(ctx.backends["cloud"].calls) == 1 and not _events(ctx, task, "read_first.held")
