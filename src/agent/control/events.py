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


def stream_events(ctx, task_id: str, since_seq: int = -1,
                  live: bool = False, idle_timeout_s: float = 30.0) -> Iterator[str]:
    """Replay what has happened, then tail what happens next.

    B5. This used to replay the log and close, which meant the SSE endpoint
    was a paginated history rather than a stream: a client attaching mid-task
    got everything up to that instant and then silence until it reconnected.

    Live output rides the same channel as lifecycle events, on purpose. A
    surface that had to poll one and subscribe to the other would show them
    out of order, and "the model said X, then called tool Y" is the one
    ordering a reader actually needs.

    `live` DEFAULTS TO FALSE, and the SSE endpoint opts in. It was briefly the
    other way round, which turned this function from "give me the events" into
    "give me events forever" under an unchanged signature — every existing
    caller joins the result into a string, so they all hung. A default that
    silently changes what an unchanged call does is the DR2 shape: the caller
    looks correct and behaves differently.

    `token` and `step` are NOT in CLIENT_EVENTS because they never enter the
    hash chain — they are a view of work in flight, not a record of it. They
    are emitted here with their own names so a client can tell a durable
    event from an ephemeral one.
    """
    import queue

    from .. import stream as bus

    for e in ctx.log.events(task_id, since_seq=since_seq):
        ce = to_client_event(e)
        if ce and ce["event"] not in ALERT_EVENTS:
            yield sse(ce)
    if not live:
        return

    q: "queue.Queue[tuple[str, dict]]" = queue.Queue()
    unsubscribe = bus.subscribe(task_id, lambda kind, data: q.put((kind, data)))
    try:
        seq = 10**9        # ephemeral ids, deliberately past any chain seq
        while True:
            try:
                kind, data = q.get(timeout=idle_timeout_s)
            except queue.Empty:
                # A comment frame, not an event. Keeps proxies from closing an
                # idle connection during a 40-second CPU turn without putting
                # anything on the stream a client has to filter out.
                yield ": keepalive" + chr(10) + chr(10)
                continue
            seq += 1
            yield sse({"event": f"live.{kind}", "seq": seq, "data": data})
    finally:
        unsubscribe()
