from __future__ import annotations

import json
import threading
import time

from agent.control.events import stream_events


def _collect(ctx, task, out):
    for chunk in stream_events(ctx, task.id, live=True, idle_timeout_s=0.2):
        out.append(chunk)


def _names(out):
    return [c.split("event: ", 1)[1].split("\n", 1)[0] for c in out if "event: " in c]


def test_events_written_after_the_replay_still_reach_the_open_stream_and_end_it(ctx, task):
    out: list[str] = []
    t = threading.Thread(target=_collect, args=(ctx, task, out), daemon=True)
    t.start()
    time.sleep(0.5)
    ctx.log.append(task.id, "tool.intent", {"tool": "list_dir", "key": "k", "step": 0, "preview": "p"})
    ctx.log.append(task.id, "task.status_changed", {"status": "done", "reason": ""})
    t.join(10)
    assert not t.is_alive(), "the stream never ended after the task finished"
    assert _names(out) == ["tool.called", "task.status"]
    assert json.loads(out[-1].split("data: ", 1)[1])["status"] == "done"


def test_a_task_that_finished_before_the_stream_opened_does_not_end_a_later_run(ctx, task):
    ctx.log.append(task.id, "task.status_changed", {"status": "done", "reason": ""})
    out: list[str] = []
    t = threading.Thread(target=_collect, args=(ctx, task, out), daemon=True)
    t.start()
    time.sleep(0.6)
    assert t.is_alive(), "an old terminal status ended the stream of a run that had not started"
    ctx.log.append(task.id, "task.status_changed", {"status": "running", "reason": ""})
    ctx.log.append(task.id, "task.status_changed", {"status": "failed", "reason": "x"})
    t.join(10)
    assert not t.is_alive()
