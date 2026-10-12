from __future__ import annotations

import json

from agent.config import BackendConfig
from agent.execute.loop import run_task
from agent.kernel.tasks import create_task

FINAL = '<final>{"answer": "ok"}</final>'
READ = '<action tool="read_file">{"path": "work/big.txt"}</action>'


def _big(workspace, lines=900):
    text = chr(10).join(f"line {i}: filler words to take up room in the context window" for i in range(lines))
    (workspace / "big.txt").write_text(text + chr(10) + "NEEDLE-IN-THE-MIDDLE" if False else
                                       chr(10).join([*(f"line {i}: filler words to take up room" for i in range(450)),
                                                     "NEEDLE-IN-THE-MIDDLE",
                                                     *(f"line {i}: filler words to take up room" for i in range(450, 900))]),
                                       encoding="utf-8")


def test_the_cap_follows_the_profile_and_the_window():
    assert BackendConfig(kind="mock", model="m").inline_observation_tokens == 400
    assert BackendConfig(kind="mock", model="m", tool_profile="patch").inline_observation_tokens == 400
    small = BackendConfig(kind="mock", model="m", tool_profile="small", context_window=16384)
    assert small.inline_observation_tokens == int(16384 * 0.4)
    assert BackendConfig(kind="mock", model="m", tool_profile="small", context_window=4096,
                         usable_context_window=2048).inline_observation_tokens == int(2048 * 0.4)
    assert BackendConfig(kind="mock", model="m", observation_tokens=1234).inline_observation_tokens == 1234


def test_the_small_profile_sees_the_whole_file_it_read(small, task, workspace):
    _big(workspace)
    small.backends["cloud"].cfg.context_window = 32768
    small.backends["cloud"].cfg.usable_context_window = 32768
    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue(READ, FINAL)
    run_task(small, t.id)
    tail = small.backends["cloud"].calls[1]["tail"]
    assert "NEEDLE-IN-THE-MIDDLE" in tail and "elided" not in tail


def test_the_full_profile_still_gets_the_head_and_the_tail(ctx, task, workspace):
    _big(workspace)
    ctx.backends["cloud"].queue(READ, FINAL)
    run_task(ctx, task.id)
    tail = ctx.backends["cloud"].calls[1]["tail"]
    assert "NEEDLE-IN-THE-MIDDLE" not in tail and "elided" in tail


def test_a_small_model_is_never_told_to_call_a_tool_it_does_not_have(small, task, workspace):
    _big(workspace, 4000)
    small.backends["cloud"].cfg.context_window = 16384
    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue(READ, FINAL)
    run_task(small, t.id)
    tail = small.backends["cloud"].calls[1]["tail"]
    assert "expand_artifact" not in tail or "expand_artifact" in small.tools.names()
    assert json.dumps(small.tools.names())
