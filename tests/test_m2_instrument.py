"""M2 acceptance — the numbers are real or they are nothing."""

from __future__ import annotations

from agent.instrument.telemetry import compute_cost
from agent.kernel.types import TokenUsage


def test_cost_math():
    u = TokenUsage(
        input_tokens=1000, output_tokens=500, cache_read_tokens=10000,
        cache_write_tokens=0,
    )
    cfg = {
        "input_cost_per_mtok": 3.0,
        "output_cost_per_mtok": 15.0,
        "cache_read_per_mtok": 0.30,
        "cache_write_per_mtok": 3.75,
    }
    assert abs(compute_cost(u, cfg) - (0.003 + 0.0075 + 0.003)) < 1e-9


def test_four_token_types_are_priced_separately():
    """A blended rate would pass a total-tokens test and still be wrong."""
    cfg = {
        "input_cost_per_mtok": 5.0, "output_cost_per_mtok": 25.0,
        "cache_read_per_mtok": 0.5, "cache_write_per_mtok": 6.25,
    }
    cheap = compute_cost(TokenUsage(cache_read_tokens=1_000_000), cfg)
    dear = compute_cost(TokenUsage(output_tokens=1_000_000), cfg)
    assert cheap == 0.5 and dear == 25.0


def test_no_call_bypasses_telemetry(ctx, task, scripted):
    """calls-table rows must equal the number of backend invocations."""
    from agent.execute.loop import run_task

    scripted("cloud", '<action tool="list_dir">{"path": "work"}</action>',
             '<final>{"answer": "done"}</final>')
    run_task(ctx, task.id)

    spy_calls = sum(
        len(getattr(b, "calls", [])) for b in ctx.backends.values()
    )
    rows = ctx.conn.execute(
        "SELECT COUNT(*) FROM calls WHERE task_id=?", (task.id,)
    ).fetchone()[0]
    assert rows == spy_calls > 0


def test_metrics_split_paid_and_free(ctx, task, scripted):
    from agent.execute.loop import run_task

    scripted("cloud", '<final>{"answer": "x"}</final>')
    run_task(ctx, task.id)
    m = ctx.telemetry.metrics(task.id)
    assert m.paid_tokens > 0
    assert m.cost_usd > 0
    assert m.free_tokens == 0  # nothing was routed local for purpose "step"
