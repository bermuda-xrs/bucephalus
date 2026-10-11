from __future__ import annotations

import json

import httpx

from agent.config import BackendConfig
from agent.execute.loop import run_task
from agent.execute.schema import build_action_schema
from agent.execute.tools import SMALL_TOOLS, default_registry
from agent.kernel.tasks import create_task
from agent.models.ollama import OllamaBackend

FINAL = '<final>{"answer": "ok"}</final>'


def test_one_schema_has_a_branch_per_tool_and_a_final():
    reg = default_registry()
    s = build_action_schema(reg, code_as_action=False)
    branches = s["anyOf"]
    tools = [b["properties"]["tool"]["const"] for b in branches if b["properties"]["kind"]["const"] == "action"]
    assert sorted(tools) == reg.names() and "run_code" in tools
    finals = [b for b in branches if b["properties"]["kind"]["const"] == "final"]
    assert len(finals) == 1 and finals[0]["required"] == ["kind", "answer"]


def test_each_tool_branch_carries_that_tools_own_argument_schema():
    reg = default_registry()
    s = build_action_schema(reg, code_as_action=False)
    by = {b["properties"]["tool"]["const"]: b for b in s["anyOf"] if "tool" in b["properties"]}
    assert by["read_file"]["properties"]["args"] == reg.get("read_file").schema
    assert by["read_file"]["required"] == ["kind", "tool", "args"]


def test_the_code_branch_exists_only_with_code_as_action():
    reg = default_registry()
    with_code = build_action_schema(reg, code_as_action=True)["anyOf"]
    without = build_action_schema(reg, code_as_action=False)["anyOf"]
    assert any(b["properties"]["kind"].get("const") == "code" for b in with_code)
    assert not any(b["properties"]["kind"].get("const") == "code" for b in without)


def test_the_small_profile_schema_names_exactly_its_tools(small):
    s = build_action_schema(small.tools, code_as_action=small.code_as_action)
    tools = [b["properties"]["tool"]["const"] for b in s["anyOf"] if "tool" in b["properties"]]
    assert sorted(tools) == sorted(SMALL_TOOLS)


def _local(small, constrained=True):
    small.backends["cloud"].cfg.trust_tier = "local"
    small.backends["cloud"].cfg.model_server = "ollama"
    small.config.mechanisms.constrained_calls = constrained


def test_a_local_backend_is_handed_the_tool_schema(small, task):
    _local(small)
    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue('{"kind": "final", "answer": "ok"}')
    run_task(small, t.id)
    schema = small.backends["cloud"].calls[0]["schema"]
    assert "anyOf" in schema and "write_file" in json.dumps(schema)
    assert "<code>" not in small.backends["cloud"].calls[0]["tail"]


def test_the_mechanism_can_be_ablated_to_the_old_generic_schema(small, task):
    _local(small, constrained=False)
    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue('{"kind": "final", "answer": "ok"}')
    run_task(small, t.id)
    schema = small.backends["cloud"].calls[0]["schema"]
    assert "anyOf" not in schema and "constrained_calls" in small.config.mechanisms.ablated()


def _ollama_body(cfg):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        lines = [{"message": {"content": "{}"}, "done": False}, {"done": True, "prompt_eval_count": 3, "eval_count": 1}]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    backend = OllamaBackend("t", cfg, client=httpx.Client(transport=httpx.MockTransport(handler)))
    backend.complete("prefix", "tail", schema={"type": "object"})
    return seen


def test_every_ollama_call_sets_num_ctx_explicitly_from_the_budgeted_window():
    body = _ollama_body(BackendConfig(kind="ollama", model="m", endpoint="http://x", context_window=131072,
                                      usable_context_window=16384))
    assert body["options"]["num_ctx"] == 16384 and body["format"] == {"type": "object"}


def test_without_a_usable_window_the_full_window_is_sent():
    body = _ollama_body(BackendConfig(kind="ollama", model="m", endpoint="http://x", context_window=32768))
    assert body["options"]["num_ctx"] == 32768
