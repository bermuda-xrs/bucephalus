from __future__ import annotations

import pytest

from agent.config import BackendConfig
from agent.context import AgentContext
from agent.execute.loop import run_task
from agent.execute.tools import tool_calc
from test_first_holds import READ, TOTAL, _events, _task

CODE = '{"kind": "action", "tool": "run_code", "args": {"code": "print(3 + 4)"}}'
CALC = '{"kind": "action", "tool": "calc", "args": {"expression": "3 + 4"}}'
SCHEMA = {"type": "object", "properties": {"total": {"type": "integer"}}}


@pytest.mark.parametrize("expr,value", [("3 + 4", 7), ("2 ** 10", 1024), ("7 // 2", 3), ("7 % 4", 3),
                                        ("-(1.5 * 4) / 3", -2.0), ("10 / 4", 2.5), ("123456789 * 987654321", 121932631112635269)])
def test_calc_is_exact_on_plain_arithmetic(expr, value):
    assert tool_calc(None, None, expr) == {"ok": True, "expression": expr, "value": value}


@pytest.mark.parametrize("expr", ["__import__('os')", "a + 1", "len('x')", "1 if 1 else 2", "[1][0]", "9 ** 9 ** 9",
                                  "(10 ** 999) ** 999", "1 / 0", "'a' * 3", "True + 1", "1 +", "1" * 3000])
def test_calc_refuses_anything_else(expr):
    out = tool_calc(None, None, expr)
    assert out["ok"] is False and out["error"]


def test_calc_is_offered_only_when_declared_on_any_kit(tmp_path, workspace, ctx, small):
    from conftest import make_config

    assert "calc" not in ctx.tools.names() and "calc" not in small.tools.names()
    assert not BackendConfig(kind="mock", model="m", tool_profile="small").on("calc_tool")
    for profile in ("full", "small"):
        cfg = make_config(tmp_path / profile)
        cfg.backends["cloud"].tool_profile, cfg.backends["cloud"].calc_tool = profile, True
        c = AgentContext(cfg)
        try:
            assert "calc" in c.tools.names(), profile
        finally:
            c.close()


def test_on_the_full_kit_a_numeric_answer_waits_for_run_code_and_is_released_by_it(ctx, workspace):
    (workspace / "a.txt").write_text("3,4", encoding="utf-8")
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.backends["cloud"].cfg.compute_first = True
    t = _task(ctx, goal="add the numbers", output_schema=SCHEMA)
    ctx.backends["cloud"].queue(READ, TOTAL, CODE, TOTAL)
    assert run_task(ctx, t.id).status.startswith("done")
    assert len(_events(ctx, t, "compute_first.held")) == 1
    note = ctx.backends["cloud"].calls[2]["tail"][-600:]
    assert "run_code" in note and "run_command" not in note


def test_a_write_receipt_that_quotes_the_old_file_is_untrusted(small, workspace):
    from test_first_holds import ANSWER, WRITE

    (workspace / "a.txt").write_text("old line from somewhere else", encoding="utf-8")
    small.backends["cloud"].cfg.write_diff = True
    t = _task(small, goal="change a.txt")
    small.backends["cloud"].queue(READ, WRITE, ANSWER)
    run_task(small, t.id)
    written = [e for e in _events(small, t, "artifact.written") if e["kind"] == "tool_output"]
    assert len(written) >= 2 and written[1]["taint"] == "untrusted"


def test_a_calc_result_releases_the_compute_hold(tmp_path, workspace):
    from conftest import make_config

    cfg = make_config(tmp_path)
    cfg.backends["cloud"].compute_first = cfg.backends["cloud"].calc_tool = True
    c = AgentContext(cfg)
    try:
        (workspace / "a.txt").write_text("3,4", encoding="utf-8")
        t = _task(c, goal="add the numbers", output_schema=SCHEMA)
        c.backends["cloud"].queue(READ, CALC, TOTAL)
        assert run_task(c, t.id).status.startswith("done")
        assert not _events(c, t, "compute_first.held")
    finally:
        c.close()
