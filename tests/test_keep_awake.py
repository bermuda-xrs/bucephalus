from __future__ import annotations

import ctypes
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
TIMED = ("noise.py", "ladder.py", "g1_ablation_run.py", "run.py", "profile_run.py", "g1_close.py")


def test_it_holds_the_system_awake_and_releases_it(monkeypatch):
    from agent import awake

    calls = []
    monkeypatch.setattr(awake, "_set", lambda flags: calls.append(flags) or 0)
    monkeypatch.setattr(sys, "platform", "win32")
    with awake.keep_awake():
        assert calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED]
    assert calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS]


def test_it_releases_when_the_run_raises(monkeypatch):
    from agent import awake

    calls = []
    monkeypatch.setattr(awake, "_set", lambda flags: calls.append(flags) or 0)
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(RuntimeError):
        with awake.keep_awake():
            raise RuntimeError("boom")
    assert calls[-1] == ES_CONTINUOUS


def test_elsewhere_it_does_nothing(monkeypatch):
    from agent import awake

    calls = []
    monkeypatch.setattr(awake, "_set", lambda flags: calls.append(flags) or 0)
    monkeypatch.setattr(sys, "platform", "linux")
    with awake.keep_awake():
        pass
    assert calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows power API")
def test_the_real_call_is_in_force_while_held_and_gone_after():
    from agent import awake

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.SetThreadExecutionState.restype = ctypes.c_uint
    with awake.keep_awake():
        held = k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    assert held & ES_SYSTEM_REQUIRED, "the requirement was not in force while the run held it"
    after = k32.SetThreadExecutionState(ES_CONTINUOUS)
    assert not after & ES_SYSTEM_REQUIRED, "the requirement was not released"


@pytest.mark.parametrize("name", TIMED)
def test_every_timed_runner_holds_the_machine_awake(name):
    src = (ROOT / "bench" / name).read_text(encoding="utf-8")
    assert re.search(r"keep_awake\(\)", src), f"bench/{name} is a timed run and does not keep the machine awake"
