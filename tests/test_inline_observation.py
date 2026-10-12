from __future__ import annotations

import json

from agent.config import BackendConfig
from agent.execute.loop import run_task
from agent.kernel.tasks import create_task

FINAL = '<final>{"answer": "ok"}</final>'
READ = '<action tool="read_file">{"path": "work/big.txt"}</action>'


def _big(workspace, lines=300):
    half = lines // 2
    body = [*(f"line {i}: filler words to take up room" for i in range(half)), "NEEDLE-IN-THE-MIDDLE",
            *(f"line {i}: filler words to take up room" for i in range(half, lines))]
    (workspace / "big.txt").write_text(chr(10).join(body), encoding="utf-8")


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


def test_a_long_read_never_forces_a_session_boundary_that_drops_it(small, task, workspace):
    _big(workspace, 3000)
    cfg = small.backends["cloud"].cfg
    cfg.context_window, cfg.usable_context_window = 16384, 16384
    small.config.limits.context_fill_ratio = 0.28
    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue(READ, FINAL)
    run_task(small, t.id)
    opened = [r[0] for r in small.conn.execute(
        "SELECT json_extract(payload_json, '$.reason') FROM events WHERE task_id=? AND type='session.opened'", (t.id,))]
    assert opened == ["start"], f"the observation pushed the context over the ceiling: {opened}"
    tail = small.backends["cloud"].calls[1]["tail"]
    assert "elided" in tail and "search" in tail.split("elided")[1][:300]


def test_the_cap_stays_under_the_compaction_ceiling(small):
    from agent.execute.loop import _inline_cap

    small.backends["cloud"].cfg.context_window = 16384
    small.backends["cloud"].cfg.usable_context_window = 16384
    small.config.limits.context_fill_ratio = 0.28
    assert _inline_cap(small) <= int(16384 * 0.28 * 0.5)


def test_an_elided_summary_fits_its_budget_however_long_the_output(ctx, task):
    from agent.kernel.types import Taint
    from agent.ledger.artifacts import project, write_artifact
    from agent.tokens import count_tokens

    for lines in (100, 3000, 30000):
        text = chr(10).join(f"row {i} with some words in it" for i in range(lines))
        aid = write_artifact(ctx.conn, task.id, "tool_output", text, Taint.UNTRUSTED, {})
        out = project(ctx.conn, aid, mode="summary", max_tokens=400)
        assert count_tokens(out) <= 400 + 80, (lines, count_tokens(out))
        assert out.startswith("row 0 ") and out.rstrip().endswith(f"row {lines - 1} with some words in it")


def test_one_long_line_is_cut_not_dropped(ctx, task):
    from agent.kernel.types import Taint
    from agent.ledger.artifacts import project, write_artifact
    from agent.tokens import count_tokens

    text = '{"ok": true}' + chr(10) + "---" + chr(10) + " ".join(f"item-{k} value {k}" for k in range(3000))
    aid = write_artifact(ctx.conn, task.id, "tool_output", text, Taint.UNTRUSTED, {})
    out = project(ctx.conn, aid, mode="summary", max_tokens=400)
    assert "item-0 value 0" in out and "item-2999 value 2999" in out
    assert 250 <= count_tokens(out) <= 480


def test_the_cap_without_an_executor_is_four_hundred_and_never_under_two_hundred(ctx):
    from agent.execute.loop import _inline_cap

    cloud = ctx.backends.pop("cloud")
    try:
        assert _inline_cap(ctx) == 400
    finally:
        ctx.backends["cloud"] = cloud
    cloud.cfg.context_window = cloud.cfg.usable_context_window = 1000
    ctx.config.limits.context_fill_ratio = 0.28
    assert _inline_cap(ctx) == 200
