"""Task profiling — what kind of work is this step, and how long will it take?

The router must track observed output length PER PURPOSE, not a global
tokens/sec, or it will systematically underestimate the local tier. A 3B model
that reasons for 2,000 tokens before answering is slower than an 8B answering
in 200. A smaller model is not automatically a faster one.
"""

from __future__ import annotations

from dataclasses import dataclass

# The purpose vocabulary. The allowlist is expressed in these terms, so a new
# purpose is off-limits to the local tier until it is measured and added.
PURPOSES = frozenset(
    {
        "step",  # a turn of the main loop
        "plan",  # drafting a plan — never local until measured
        "review",  # auto-review decision — never local
        "monitor",  # out-of-band monitor
        "summarize",
        "classify",
        "extract",
        "compress_tool_output",
        "draft_candidate",
        "author_tests",
        "verify",
        "delegate",
    }
)

DEFAULT_OUTPUT_TOKENS = 512


@dataclass
class TaskProfile:
    purpose: str
    est_input_tokens: int = 0
    requires_tools: bool = False
    requires_planning: bool = False


def estimate_seconds(ctx, tier: str, purpose: str) -> float:
    """Estimate from OBSERVED output length for this purpose.

    Track output length per purpose from the calls table, or the router will
    systematically underestimate local cost.
    """
    p50_out = ctx.telemetry.median_output_tokens(tier, purpose) or DEFAULT_OUTPUT_TOKENS
    backend = ctx.backends[tier]
    # Rates from real work first; the configured assumption only until some
    # exists. Deliberately never probes — routing must not cost a model call.
    tps = ctx.telemetry.observed_tokens_per_sec(tier, purpose) \
        or backend.measure_throughput()
    if tps <= 0:
        return float("inf")  # unreachable backend: never route to it
    return p50_out / tps
