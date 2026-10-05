from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent import schedule as sched
from agent.runlock import (
    RunLock,
    RunLockBusy,
    lock_status,
    measurement_lock_path,
    status_path,
)
from test_schedule import _base, _sched

ROOT = Path(__file__).resolve().parents[1]


def test_the_holder_is_readable_without_taking_the_lock(tmp_path):
    path = tmp_path / ".measurement.lock"
    lock = RunLock(path, "noise floor").acquire()
    try:
        st = lock_status(path)
        assert st["held"] and st["pid"] == os.getpid() and st["label"] == "noise floor"
        with pytest.raises(RunLockBusy):
            RunLock(path, "probe").acquire()
    finally:
        lock.release()
    assert lock_status(path)["held"] is False
    assert not status_path(path).exists(), "release clears the published holder"


def test_a_dead_holder_reads_as_stale_not_held(tmp_path):
    dead = int(subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                              capture_output=True, text=True).stdout.strip())
    path = tmp_path / ".measurement.lock"
    status_path(path).write_text(json.dumps({"pid": dead, "label": "noise floor",
                                             "started_at": 0}), encoding="utf-8")
    st = lock_status(path)
    assert st["held"] is False and st.get("stale") is True


def test_measurements_share_one_lock_whatever_their_output(tmp_path):
    assert measurement_lock_path(tmp_path) == tmp_path / ".measurement.lock"
    for script in ("bench/noise.py", "bench/run.py"):
        src = (ROOT / script).read_text(encoding="utf-8")
        assert '_out.parent / ".measurement.lock"' not in src, script
        assert "measurement_lock_path(" in src, script


def test_the_supervisor_will_not_fire_during_a_measurement(ctx):
    base = _base()
    _sched(ctx, created=base)
    lock = RunLock(measurement_lock_path(ctx.config.root), "noise floor").acquire()
    try:
        out = sched.tick(ctx, now=base + 65)
        assert [r["action"] for r in out] == ["missed"], out
        assert "measurement" in out[0]["reason"]
    finally:
        lock.release()
    assert [r["action"] for r in sched.tick(ctx, now=base + 125)] == ["fired"], (
        "and the next occurrence after it fires normally")


def test_a_fired_task_waits_for_the_measurement_to_end(ctx):
    base = _base()
    _sched(ctx, created=base)
    assert [r["action"] for r in sched.tick(ctx, now=base + 65)] == ["fired"]
    ctx.backends["cloud"].queue('<final>{"answer": "done"}</final>')
    lock = RunLock(measurement_lock_path(ctx.config.root), "calibration").acquire()
    try:
        assert sched.run_due(ctx) == [], "a scheduled task ran during a measurement"
    finally:
        lock.release()
    assert [r["status"] for r in sched.run_due(ctx)] == ["done"]
