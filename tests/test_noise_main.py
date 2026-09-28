"""bench/noise.py `main`, driven to its first decision with the machine left alone.

No test ran `main`. A line added 2026-09-06 read `ctx.config` forty-five lines
before `ctx` was assigned, so every noise-floor run since died with
UnboundLocalError before its first task - step 1 of the README's measurement
flow, unrunnable, with every test green. And its measurement lock was released
on two of its four exits, the same leak `cmd_paired` had.

The preflight calls that touch the machine - the orphan reaper, the memory
probes - are stubbed. A test must never kill a worker.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# These take the REAL checkout's measurement lock (bench/run.py and noise.py
# lock beside --config config.local.yaml). One worker at a time, or xdist
# workers contend on it and read "lock busy" (iteration 3, 2.2).
pytestmark = pytest.mark.xdist_group("real-measurement-lock")


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load():
    spec = importlib.util.spec_from_file_location("noise_main", ROOT / "bench" / "noise.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Ctx:
    def __init__(self, suite_lookup):
        cloud = type("B", (), {"keep_alive": None, "budgeted_window": 4096})()
        self.config = type("C", (), {"profile": "local", "profile_provenance": {},
                                     "backends": {"cloud": cloud}})()
        self.config.autonomy = type("A", (), {"auto_review": True,
                                              "approve_scoped_writes": False})()
        self.userdata = type("U", (), {"get_suite_by_name": staticmethod(suite_lookup)})()
        self.closed = False

    def close(self):
        self.closed = True


def _run(monkeypatch, tmp_path, suite_lookup):
    mod = _load()
    ctx = _Ctx(suite_lookup)
    monkeypatch.setattr(mod, "reap_orphans", lambda: [])
    monkeypatch.setattr(mod, "free_memory_gb", lambda: 8.0)
    monkeypatch.setattr(mod, "memory_pressure", lambda: {})
    monkeypatch.setattr(mod, "open_measurement", lambda *a, **k: ctx)
    monkeypatch.setattr(sys, "argv", [
        "noise.py", "--config", "config.local.yaml", "--suite", "no_such_suite",
        "--out", str(tmp_path / "nf.json"), "--order-seed", "1"])
    return mod.main()


def _lock_is_free():
    from agent.runlock import RunLock, measurement_lock_path

    RunLock(measurement_lock_path(Path("config.local.yaml").resolve().parent),
            "probe").acquire().release()


def test_main_reaches_its_first_decision_and_releases_the_lock(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, lambda name: None) == 2, "no such suite"
    _lock_is_free()


def test_an_exception_in_main_releases_the_lock(monkeypatch, tmp_path):
    def broken(name):
        raise RuntimeError("the suite store is broken")

    with pytest.raises(RuntimeError, match="suite store"):
        _run(monkeypatch, tmp_path, broken)
    _lock_is_free()
