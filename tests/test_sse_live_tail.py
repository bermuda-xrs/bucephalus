"""The SSE stream replays the record, then tails the live bus (iteration 3,
2.4: the live half had no in-process test, and the console streams from it).
"""

from __future__ import annotations

from agent import stream as bus
from agent.control.events import stream_events
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract


def test_the_stream_replays_then_delivers_live_output_in_order(ctx):
    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=60))
    ctx.log.append(t.id, "tool.intent", {"tool": "read_file", "step": 0, "key": "k", "preview": "p"})
    gen = stream_events(ctx, t.id, live=True, idle_timeout_s=0.2)
    replayed = next(gen)
    assert "event: tool.called" in replayed
    rest = next(gen)                                  # nothing live yet: a keepalive comment
    assert rest.startswith(":")
    bus.publish(t.id, "waiting", {"step": 0, "backend": "cloud"})
    bus.publish(t.id, "token", {"text": "hello"})
    a, b = next(gen), next(gen)
    assert "event: live.waiting" in a
    assert "event: live.token" in b and '"hello"' in b
    # Ephemeral ids sit past any chain seq, so a resume never confuses them.
    assert int(b.split("\n")[0].split(": ")[1]) > 10**9
    gen.close()


def test_the_monitor_never_rides_the_stream(ctx):
    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=60))
    out = "".join(stream_events(ctx, t.id))
    assert "monitor" not in out
