from __future__ import annotations

DEFAULT_OUTPUT_TOKENS = 512


def estimate_seconds(ctx, tier: str, purpose: str) -> float:
    p50_out = ctx.telemetry.median_output_tokens(tier, purpose) or DEFAULT_OUTPUT_TOKENS
    backend = ctx.backends[tier]
    tps = ctx.telemetry.observed_tokens_per_sec(tier, purpose) \
        or backend.measure_throughput()
    if tps <= 0:
        return float("inf")
    return p50_out / tps
