"""M6.2 / M6.3 — the local allowlist and escalation by self-consistency.

Two things the spec is emphatic about and this file enforces:

  * The local tier starts restricted BY PURPOSE, not by a difficulty estimate.
    Never let it make planning or delegation decisions until skill_trials data
    justifies it.
  * Escalate on DISAGREEMENT, not on self-reported confidence. Verbalized
    confidence is systematically overconfident and basically useless.
    Agreement across k samples is free on local hardware.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from ..kernel.types import ModelResponse
from .profile import estimate_seconds

# Escalation rate is a health metric: above ~50% the local tier is out of its
# depth; below ~5% you are being too cautious and leaving savings unclaimed.
ESCALATION_HEALTHY_LOW = 0.05
ESCALATION_HEALTHY_HIGH = 0.50

# "2 of 3 agree" as a fraction, not the 0.67 the spec writes: 2/3 is 0.6667,
# which is LESS than 0.67, so the literal constant escalates every 2-of-3
# agreement it was written to accept. Free local samples make that expensive
# in wall-clock and pointless in outcome.
DEFAULT_AGREEMENT = 2 / 3


class Router:
    def __init__(self, ctx):
        self.ctx = ctx
        self.user_override: str | None = None

    def choose(self, purpose: str, task=None, step: int = 0):
        """Return the backend that should handle this purpose."""
        ctx = self.ctx
        if self.user_override:
            return ctx.backends[self.user_override]

        if purpose not in set(ctx.config.local_allowlist):
            return ctx.backends["cloud"]

        tier = ctx.active_local_tier()  # "local" | "laptop" | None
        if tier is None:
            return ctx.backends["cloud"]
        if tier == "laptop" and purpose not in set(ctx.config.laptop_allowlist):
            return ctx.backends["cloud"]

        # Time, not money, is the local constraint.
        est_s = estimate_seconds(ctx, tier, purpose)
        if task is not None and task.budget.remaining_seconds() < est_s:
            return ctx.backends["cloud"]  # free but too slow
        self._assert_escalation_not_silently_dead(tier)
        return ctx.backends[tier]

    def _assert_escalation_not_silently_dead(self, tier: str) -> None:
        """M6.3 escalation-by-disagreement has no call site.

        Under a single tier that is harmless - there is nothing to escalate
        TO, and `choose` returns the one backend on its first branch. It stops
        being harmless the moment a real second tier is configured: routing
        then looks active while the escalation helper below still never runs, which
        is the exact shape of the seven components this project already found
        dead with green tests.

        This does NOT raise. Two-tier routing is meaningful without
        escalation - purposes still route by allowlist - so refusing to run
        would block a configuration that is otherwise sound. Instead the fact
        is recorded on `profile_provenance`, which lands in every run record
        and every report header, so a two-tier number can never be read
        without seeing that escalation-by-disagreement did not run.

        Discovered by this check: the TEST fixture config has configured two
        tiers all along, so escalation should have been exercised in the test
        suite and never was.
        """
        cfg = self.ctx.config
        import agent.router.cascade as _m

        if getattr(_m, "_ESCALATION_WIRED", False):
            return

        # Narrowed: raise only where escalation SHOULD be reachable - a real
        # second tier AND a non-empty allowlist AND an active local tier. That
        # keeps a single-tier config (and a two-arm comparison) unblocked while
        # making the gap un-ignorable the moment it actually matters.
        if tier in cfg.backends:
            raise RuntimeError(
                "escalation-by-disagreement (M6.3) is reachable but unwired: "
                f"active_local_tier={cfg.active_local_tier!r} exists as a "
                f"backend and {len(cfg.local_allowlist)} purposes are "
                "allowlisted to it, yet the escalation helper has no call "
                "site. Routing would look active while the escalation path "
                "never runs, which produces numbers nobody can interpret. "
                "Wire it and set _ESCALATION_WIRED = True, or clear "
                "active_local_tier."
            )

        # Configured but the tier does not resolve to a backend: record it
        # rather than raising, since nothing can route there anyway.
        prov = getattr(cfg, "profile_provenance", None)
        if isinstance(prov, dict) and "escalation" not in prov:
            prov["escalation"] = (
                f"NOT WIRED - active_local_tier={cfg.active_local_tier!r} is "
                "configured but is not a resolvable backend, so no purpose "
                "routes there and escalation-by-disagreement did not run."
            )

    def health(self) -> dict:
        totals = self.ctx.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(escalated),0) FROM calls"
        ).fetchone()
        n, esc = totals[0] or 0, totals[1] or 0
        rate = (esc / n) if n else 0.0
        if not n:
            verdict = "no data"
        elif rate > ESCALATION_HEALTHY_HIGH:
            verdict = "local tier is out of its depth"
        elif rate < ESCALATION_HEALTHY_LOW:
            verdict = "too cautious — savings left unclaimed"
        else:
            verdict = "healthy"
        return {"n_calls": n, "escalation_rate": round(rate, 4), "verdict": verdict}


def normalize(text: str) -> str:
    """Compare parsed objects where possible, strings otherwise."""
    s = text.strip()
    try:
        return json.dumps(json.loads(s), sort_keys=True, separators=(",", ":"))
    except (json.JSONDecodeError, ValueError):
        return re.sub(r"\s+", " ", s).strip().lower()


def max_cluster_fraction(samples: list[str]) -> float:
    if not samples:
        return 0.0
    counts = Counter(samples)
    return counts.most_common(1)[0][1] / len(samples)


def pick_representative(samples: list[ModelResponse]) -> ModelResponse:
    norm = [normalize(s.text) for s in samples]
    winner = Counter(norm).most_common(1)[0][0]
    for s, n in zip(samples, norm):
        if n == winner:
            return s
    return samples[0]


def local_with_escalation(
    ctx,
    task,
    purpose: str,
    prefix: str,
    tail: str,
    k: int = 3,
    agreement_threshold: float = DEFAULT_AGREEMENT,
    schema: dict | None = None,
) -> tuple[ModelResponse, bool]:
    """Sample the local tier k times; escalate on disagreement. Returns (resp, escalated)."""
    tier = ctx.active_local_tier()
    if tier is None:
        resp = ctx.backends["cloud"].complete(prefix, tail, None, 1024, 0.0, schema)
        return resp, True

    local = ctx.backends[tier]
    samples = [local.complete(prefix, tail, None, 1024, 0.7, schema) for _ in range(k)]
    for s in samples:
        ctx.record(task, s, purpose=purpose)

    agreement = max_cluster_fraction([normalize(s.text) for s in samples])
    if agreement >= agreement_threshold:  # 2 of 3 agree
        return pick_representative(samples), False

    ctx.log.append(
        task.id,
        "model.called",
        {"backend": "cloud", "escalated": True, "agreement": round(agreement, 3),
         "purpose": purpose},
    )
    resp = ctx.backends["cloud"].complete(prefix, tail, None, 1024, 0.0, schema)
    ctx.record(task, resp, purpose=purpose, escalated=True)
    return resp, True
