from __future__ import annotations

from agent.router.profile import estimate_seconds


def test_routing_makes_no_model_calls(ctx, task):
    before = {n: len(getattr(b, "calls", [])) for n, b in ctx.backends.items()}

    for purpose in ("summarize", "classify", "extract", "compress_tool_output"):
        ctx.router.choose(purpose, task)
    estimate_seconds(ctx, "local", "summarize")

    after = {n: len(getattr(b, "calls", [])) for n, b in ctx.backends.items()}
    assert after == before, f"routing called a model: {before} -> {after}"


def test_estimate_uses_the_configured_assumption_before_any_data(ctx):
    ctx.backends["local"].cfg.assumed_tokens_per_sec = 4.0
    ctx.backends["local"]._tokens_per_sec = None  # noqa: SLF001

    assert abs(estimate_seconds(ctx, "local", "summarize") - 128.0) < 1.0


def test_observed_rate_beats_the_assumption(ctx, task):
    from conftest import record_call

    from agent.kernel.types import TokenUsage

    ctx.backends["local"].cfg.assumed_tokens_per_sec = 100.0
    ctx.backends["local"]._tokens_per_sec = None  # noqa: SLF001

    record_call(ctx.conn, task.id, "local", "summarize",
                TokenUsage(output_tokens=100), 0.0, 10_000)

    observed = ctx.telemetry.observed_tokens_per_sec("local", "summarize")
    assert abs(observed - 10.0) < 0.1

    est = estimate_seconds(ctx, "local", "summarize")
    assert abs(est - 10.0) < 1.0


def test_probe_is_explicit_and_does_call_the_model(ctx):
    b = ctx.backends["local"]
    before = len(b.calls)
    b.probe_throughput()
    assert len(b.calls) == before + 1
