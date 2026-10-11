from __future__ import annotations

from .exposure import UNRESTRICTED, ToolPolicy

LOCAL = "local"
TELEGRAM = "telegram"

CHANNEL_ORIGINS = frozenset({TELEGRAM})

SCHEDULE = "schedule"

UNATTENDED_ORIGINS = CHANNEL_ORIGINS | {SCHEDULE}

SCHEDULE_DENIED = frozenset({
    "git_push",
    "append_to_outbox",
    "memory_write",
    "memory_consolidate",
    "delegate",
})

CHANNEL_DENIED = frozenset({
    "run_code",
    "git_push",
    "append_to_outbox",
    "memory_write",
    "memory_consolidate",
    "delegate",
})

CHANNEL_BUDGET_FRACTION = 0.2


def is_channel(origin: str | None) -> bool:
    return (origin or LOCAL) in CHANNEL_ORIGINS


def is_unattended(origin: str | None) -> bool:
    return (origin or LOCAL) in UNATTENDED_ORIGINS


def policy_for(origin: str | None) -> ToolPolicy:
    if (origin or LOCAL) == SCHEDULE:
        return ToolPolicy(denied=SCHEDULE_DENIED, reason="scheduled task, unattended", denied_prefixes=("mcp__",))
    if not is_channel(origin):
        return UNRESTRICTED
    return ToolPolicy(denied=CHANNEL_DENIED, reason=f"{origin} channel task", denied_prefixes=("mcp__",))


def clamp_budget(budget, limits, origin: str | None):
    if not is_channel(origin):
        return budget
    ceiling_usd = limits.default_task_budget_usd * CHANNEL_BUDGET_FRACTION
    ceiling_s = limits.default_task_budget_seconds * CHANNEL_BUDGET_FRACTION
    return budget.model_copy(update={
        "usd": min(budget.usd, ceiling_usd),
        "seconds": min(budget.seconds, int(ceiling_s)),
    })
