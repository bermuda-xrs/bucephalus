from __future__ import annotations

import json

import pytest
from test_ollama_retry import Client, Stream

from agent.config import BackendConfig
from agent.execute.loop import run_task
from agent.execute.schema import build_action_schema
from agent.execute.tools import apply_profile, default_registry
from agent.kernel.tasks import create_task
from agent.models.ollama import OllamaBackend

JFINAL = '{"kind": "final", "answer": "ok"}'


def _act(tool, carry=None, thought=None, **args):
    d = {"kind": "action", "tool": tool, "args": args}
    if thought is not None:
        d = {"thought": thought, **d}
    if carry is not None:
        d["carry"] = carry
    return json.dumps(d)


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


@pytest.fixture
def sm(small):
    b = small.backends["cloud"].cfg
    b.trust_tier, b.model_server = "local", "ollama"
    small.config.limits.max_steps = 12
    return small


def _task(ctx, task):
    return create_task(ctx.conn, ctx.log, task.contract, task.budget)


def test_b2_is_on_for_the_small_profile_only_unless_declared():
    assert BackendConfig(kind="mock", model="m").requires_carry is False
    assert BackendConfig(kind="mock", model="m", tool_profile="small").requires_carry is True
    assert BackendConfig(kind="mock", model="m", tool_profile="small", carry_field=False).requires_carry is False
    assert BackendConfig(kind="mock", model="m", carry_field=True).requires_carry is True


def test_b1_is_off_by_default_after_the_held_out_measurement_but_can_be_declared():
    assert BackendConfig(kind="mock", model="m", tool_profile="small").requires_thought is False
    assert BackendConfig(kind="mock", model="m", tool_profile="small", reasoning_field=True).requires_thought is True


KEPT = ("compact_prompt", "evidence_pins", "stuck_patterns", "escape_check")
OFF = ("code_out_of_json", "progress_file", "evaluator_pass", "send_tool_list")


@pytest.mark.parametrize("name", KEPT)
def test_kept_items_are_on_for_the_small_profile_only_unless_declared(name):
    assert BackendConfig(kind="mock", model="m").on(name) is False
    assert BackendConfig(kind="mock", model="m", tool_profile="small").on(name) is True
    assert BackendConfig(kind="mock", model="m", tool_profile="small", **{name: False}).on(name) is False
    assert BackendConfig(kind="mock", model="m", **{name: True}).on(name) is True


@pytest.mark.parametrize("name", OFF)
def test_items_that_did_not_help_stay_off_even_for_the_small_profile(name):
    assert BackendConfig(kind="mock", model="m", tool_profile="small").on(name) is False


def test_e1_hashline_and_c3_a_wider_ceiling_are_the_small_profile_defaults():
    from agent.config import SMALL_FILL_RATIO

    small, full = BackendConfig(kind="mock", model="m", tool_profile="small"), BackendConfig(kind="mock", model="m")
    assert small.edit_style == "hashline" and full.edit_style == "default"
    assert BackendConfig(kind="mock", model="m", tool_profile="small", edit_format="default").edit_style == "default"
    assert small.fill_ratio == SMALL_FILL_RATIO == 0.5 and full.fill_ratio is None
    assert BackendConfig(kind="mock", model="m", tool_profile="small", context_fill_ratio=0.3).fill_ratio == 0.3
    b = BackendConfig(kind="mock", model="m", tool_profile="small")
    assert (b.tool_call_mode, b.tool_names) == ("constrained", "internal")


def test_the_harness_card_records_the_resolved_mechanisms(sm):
    from agent.card import harness_card

    r = harness_card(sm)["resolved"]
    assert r["compact_prompt"] is True and r["reasoning_field"] is False and r["edit_format"] == "hashline"
    assert r["context_fill_ratio"] == 0.5


def test_b1_the_thought_comes_first_and_is_bounded():
    s = build_action_schema(apply_profile(default_registry(), "small"), False, thought=300)
    for b in s["anyOf"]:
        assert list(b["properties"])[0] == "thought" and b["required"][0] == "thought"
        assert b["properties"]["thought"]["maxLength"] == 300


def test_b1_a_run_on_the_small_profile_asks_for_a_thought_and_parses_past_it(sm, task, workspace):
    sm.backends["cloud"].cfg.reasoning_field = True
    (workspace / "a.txt").write_text("hello", encoding="utf-8")
    t = _task(sm, task)
    sm.backends["cloud"].queue(_act("read_file", thought="look first", path="work/a.txt"),
                               json.dumps({"thought": "done", "kind": "final", "answer": "ok"}))
    assert run_task(sm, t.id).status.startswith("done")
    assert "thought" in sm.backends["cloud"].calls[0]["schema"]["anyOf"][0]["properties"]


def test_b2_after_an_observation_every_action_carries_notes_that_persist(sm, task, workspace):
    (workspace / "a.txt").write_text("the code is QX-4471", encoding="utf-8")
    (workspace / "b.txt").write_text("nothing here", encoding="utf-8")
    t = _task(sm, task)
    sm.backends["cloud"].queue(_act("read_file", thought="t", path="work/a.txt"),
                               _act("read_file", thought="t", carry="code QX-4471 from a.txt", path="work/b.txt"),
                               json.dumps({"thought": "t", "kind": "final", "answer": "QX-4471"}))
    assert run_task(sm, t.id).status.startswith("done")
    calls = sm.backends["cloud"].calls
    first, second = calls[0]["schema"]["anyOf"], calls[1]["schema"]["anyOf"]
    assert all("carry" not in b["properties"] for b in first)
    acts = [b for b in second if b["properties"]["kind"]["const"] == "action"]
    assert acts and all("carry" in b["required"] and b["properties"]["carry"]["maxLength"] > 0 for b in acts)
    tail = calls[2]["tail"]
    assert "code QX-4471 from a.txt" in tail
    carry_obs = tail.split("code QX-4471 from a.txt")[0].rsplit("<observation", 1)[1]
    assert "taint=untrusted" in carry_obs, "notes made from what was read carry its taint"


def test_b2_the_notes_survive_a_session_boundary(sm, task, workspace):
    (workspace / "a.txt").write_text("x", encoding="utf-8")
    sm.config.compaction_probe_step = 2
    t = _task(sm, task)
    sm.backends["cloud"].queue(_act("read_file", thought="t", path="work/a.txt"),
                               _act("read_file", thought="t", carry="KEEP-ME-123", path="work/a.txt"),
                               json.dumps({"thought": "t", "kind": "final", "answer": "ok"}))
    run_task(sm, t.id)
    assert len(_events(sm, t, "session.opened")) == 2
    assert "KEEP-ME-123" in sm.backends["cloud"].calls[-1]["tail"]


def test_b3_long_text_leaves_the_json_and_comes_back_in_a_fenced_block(sm, task, workspace):
    sm.backends["cloud"].cfg.code_out_of_json = True
    t = _task(sm, task)
    body = "import re\n\nPAT = re.compile(r'[^\\w\\s-]')\n"
    sm.backends["cloud"].queue(_act("write_file", thought="t", path="work/m.py"), "```python\n" + body + "```",
                               _act("read_file", thought="t", carry="c", path="work/m.py"),
                               json.dumps({"thought": "t", "kind": "final", "answer": "ok"}))
    run_task(sm, t.id)
    assert (workspace / "m.py").read_text(encoding="utf-8") == body
    calls = sm.backends["cloud"].calls
    wf = [b for b in calls[0]["schema"]["anyOf"] if b["properties"].get("tool", {}).get("const") == "write_file"][0]
    assert "content" not in wf["properties"]["args"]["properties"] and calls[1]["schema"] is None
    assert _events(sm, t, "action.body_fetched")[0]["tool"] == "write_file"


def test_b4_constrained_mode_sends_no_tool_list_and_native_mode_sends_ollamas(sm, task, workspace):
    t = _task(sm, task)
    sm.backends["cloud"].queue(JFINAL)
    run_task(sm, t.id)
    assert sm.backends["cloud"].calls[0]["tools"] is None
    sm.backends["cloud"].cfg.tool_call_mode = "native"
    (workspace / "a.txt").write_text("hi", encoding="utf-8")
    t2 = _task(sm, task)
    sm.backends["cloud"].queue(json.dumps({"kind": "action", "tool": "read_file", "args": {"path": "work/a.txt"}}),
                               '{"done": true}')
    assert run_task(sm, t2.id).status.startswith("done")
    call = sm.backends["cloud"].calls[-2]
    assert call["schema"] is None and {t["type"] for t in call["tools"]} == {"function"}
    assert {t["function"]["name"] for t in call["tools"]} == set(sm.tools.names())


def test_b4_ollama_tool_calls_become_actions():
    lines = [json.dumps({"message": {"content": "", "tool_calls": [
        {"function": {"name": "read_file", "arguments": {"path": "work/a.txt"}}}]}}),
        json.dumps({"done": True, "eval_count": 3, "prompt_eval_count": 9})]
    b = OllamaBackend("t", BackendConfig(kind="ollama", model="m", endpoint="http://localhost:11435"),
                      client=Client([Stream(lines)]))
    got = json.loads(b.complete("p", "t", tools=[{"type": "function", "function": {"name": "read_file"}}]).text)
    assert got == {"kind": "action", "tool": "read_file", "args": {"path": "work/a.txt"}}


def test_b5_familiar_names_reach_the_model_and_map_back(sm, task, workspace):
    sm.backends["cloud"].cfg.tool_names = "familiar"
    (workspace / "a.txt").write_text("line\n" * 4000, encoding="utf-8")
    t = _task(sm, task)
    sm.backends["cloud"].queue(_act("read", thought="t", path="work/a.txt"),
                               _act("read", thought="t", carry="c", path="work/a.txt", page=2),
                               json.dumps({"thought": "t", "kind": "final", "answer": "ok"}))
    run_task(sm, t.id)
    calls = sm.backends["cloud"].calls
    consts = {b["properties"]["tool"]["const"] for b in calls[0]["schema"]["anyOf"] if "tool" in b["properties"]}
    assert {"read", "write", "bash", "grep"} <= consts and "read_file" not in consts
    assert "read(" in calls[0]["prefix"] and "read_file(" not in calls[0]["prefix"]
    assert "read(path='work/a.txt', page=2)" in calls[1]["tail"]
    assert [e["tool"] for e in _events(sm, t, "tool.receipt")] == ["read_file", "read_file"]


def test_b6_the_compact_contract_is_a_third_of_the_tokens_and_keeps_the_rules(sm, task):
    from agent.ledger.prefix import system_contract
    from agent.tokens import count_tokens

    full, compact = system_contract(False), system_contract(False, compact=True)
    assert count_tokens(compact) * 3 <= count_tokens(full)
    for rule in ("DATA", "taint", "artifact_scope"):
        assert rule in compact
    assert "<action" not in compact
    sm.backends["cloud"].cfg.compact_prompt = True
    t = _task(sm, task)
    sm.backends["cloud"].queue(JFINAL)
    run_task(sm, t.id)
    assert compact in sm.backends["cloud"].calls[0]["prefix"] and full not in sm.backends["cloud"].calls[0]["prefix"]


def test_b4_the_harness_14_tool_list_can_be_sent_for_comparison(sm, task, workspace):
    sm.backends["cloud"].cfg.send_tool_list = True
    t = _task(sm, task)
    sm.backends["cloud"].queue(JFINAL)
    run_task(sm, t.id)
    assert sm.backends["cloud"].calls[0]["tools"] == sm.tools.schemas()
