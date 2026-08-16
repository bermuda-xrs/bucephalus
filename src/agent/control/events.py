"""M13.8 — the kernel → client event schema.

Never emit monitor.* on this channel. The monitor is invisible to the agent
AND to the agent's own event stream; its findings reach a human only through
/alerts. There is a test that greps for this.
"""

from __future__ import annotations

import json
from typing import Iterator

# The client-facing vocabulary, mapped from internal event types.
CLIENT_EVENTS = {
    "plan.drafted": "plan.drafted",
    "plan.approved": "plan.approved",
    "step.started": "step.started",
    "step.completed": "step.completed",
    "tool.intent": "tool.called",
    "review.requested": "review.requested",
    "review.decided": "review.decided",
    "delegation.opened": "delegation.opened",
    "task.budget_spent": "budget.updated",
    "task.status_changed": "task.status",
    "deviation.detected": "deviation.detected",
    "integrity.halt": "alert.integrity",  # separate channel, see below
}

# Anything matching this never goes out on the client stream.
FORBIDDEN_PREFIXES = ("monitor.",)

# Integrity alerts ride a separate channel from approvals. A broken hash chain
# is not a permission request, and if they share a visual treatment you will
# eventually approve one by muscle memory.
ALERT_EVENTS = {"alert.integrity"}


def to_client_event(internal: dict) -> dict | None:
    name = CLIENT_EVENTS.get(internal["type"])
    if name is None:
        return None
    if any(name.startswith(p) for p in FORBIDDEN_PREFIXES):
        raise AssertionError(f"attempted to emit {name} on the client channel")
    return {"event": name, "seq": internal["seq"], "data": internal["payload"]}


def sse(event: dict) -> str:
    return f"id: {event['seq']}\nevent: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"


def stream_events(ctx, task_id: str, since_seq: int = -1) -> Iterator[str]:
    for e in ctx.log.events(task_id, since_seq=since_seq):
        ce = to_client_event(e)
        if ce and ce["event"] not in ALERT_EVENTS:
            yield sse(ce)
