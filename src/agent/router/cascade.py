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

from ..kernel.events import canonical
from .profile import estimate_seconds

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
        return ctx.backends[tier]

def normalize(text: str) -> str:
    """Compare parsed objects where possible, strings otherwise."""
    s = text.strip()
    try:
        return canonical(json.loads(s))
    except (json.JSONDecodeError, ValueError):
        return re.sub(r"\s+", " ", s).strip().lower()


