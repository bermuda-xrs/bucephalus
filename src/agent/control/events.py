from __future__ import annotations

import json
from typing import Iterator

CLIENT_EVENTS = {
    "plan.drafted": "plan.drafted",
    "plan.approved": "plan.approved",
    "tool.intent": "tool.called",
    "tool.receipt": "tool.returned",
    "review.requested": "review.requested",
    "review.decided": "review.decided",
    "delegation.opened": "delegation.opened",
    "task.budget_spent": "budget.updated",
    "task.status_changed": "task.status",
    "deviation.detected": "deviation.detected",
    "integrity.halt": "alert.integrity",
}

FORBIDDEN_PREFIXES = ("monitor.",)

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
        seq = 10**9
        while True:
            try:
                kind, data = q.get(timeout=idle_timeout_s)
            except queue.Empty:
                yield ": keepalive" + chr(10) + chr(10)
                continue
            seq += 1
            yield sse({"event": f"live.{kind}", "seq": seq, "data": data})
    finally:
        unsubscribe()
