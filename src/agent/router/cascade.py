from __future__ import annotations

import json
import re

from ..kernel.events import canonical
from .profile import estimate_seconds

DEFAULT_AGREEMENT = 2 / 3

class Router:
    def __init__(self, ctx):
        self.ctx = ctx
        self.user_override: str | None = None

    def choose(self, purpose: str, task=None, step: int = 0):
        ctx = self.ctx
        if self.user_override:
            return ctx.backends[self.user_override]

        if purpose not in set(ctx.config.local_allowlist):
            return ctx.backends["cloud"]

        tier = ctx.active_local_tier()
        if tier is None:
            return ctx.backends["cloud"]
        if tier == "laptop" and purpose not in set(ctx.config.laptop_allowlist):
            return ctx.backends["cloud"]

        est_s = estimate_seconds(ctx, tier, purpose)
        if task is not None and task.budget.remaining_seconds() < est_s:
            return ctx.backends["cloud"]
        return ctx.backends[tier]

def normalize(text: str) -> str:
    s = text.strip()
    try:
        return canonical(json.loads(s))
    except (json.JSONDecodeError, ValueError):
        return re.sub(r"\s+", " ", s).strip().lower()
