"""A stored PID may now belong to a different process (AUDIT F4, D42).

The reaper learned this once: `orphan_pids` compares start times, because a
"parent" that started AFTER its child is a recycled pid. Every other site that
signals a pid it did not just spawn walked the same (pid, ppid) table with no
such check - the desktop's shutdown sweep, the reaper's own kill, and
`taskkill /T` itself, which follows parent ids blindly. Each could kill an
unrelated process that happened to inherit a dead process's pid.

One rule now, in `proc.descendants`: a child belongs to a tree only if it
started no earlier than its parent. And `kill_tree(pid, expect_start=...)`
signals nothing when the pid's creation time is not the one recorded.
"""

from __future__ import annotations

import types

from agent import desktop, proc

# root 100 (t=50) -> 200 (t=60) -> 201 (t=70): a real tree.
# 300 names 100 as parent but started at t=10, BEFORE 100 existed: its real
# parent died and 100 is a recycled pid. 301 is 300's child.
TABLE = [(100, 1, "app"), (200, 100, "child"), (201, 200, "grandchild"),
         (300, 100, "impostor"), (301, 300, "impostor-child")]
START = {1: 1, 100: 50, 200: 60, 201: 70, 300: 10, 301: 20}


def _fake_os(monkeypatch):
    monkeypatch.setattr(proc, "process_table", lambda: TABLE)
    monkeypatch.setattr(proc, "process_start_time", lambda pid: START.get(pid))


def test_a_tree_does_not_adopt_a_process_through_a_recycled_parent(monkeypatch):
    _fake_os(monkeypatch)
    assert sorted(proc.descendants(100)) == [200, 201]


def test_the_desktop_sweep_uses_the_same_rule(monkeypatch):
    _fake_os(monkeypatch)
    monkeypatch.setattr(desktop.os, "getpid", lambda: 100)
    assert sorted(desktop.descendants()) == [200, 201]


def test_kill_tree_leaves_a_reused_pid_alone(monkeypatch):
    _fake_os(monkeypatch)
    sent = []
    monkeypatch.setattr(proc, "_platform", types.SimpleNamespace(
        kill_tree_signal=lambda pid, t, *a: sent.append(pid), alive=lambda pid: False))
    assert proc.kill_tree(300, expect_start=999) is True   # the process we meant is gone
    assert sent == [], "signalled a pid that now belongs to another process"
    proc.kill_tree(200, expect_start=60)
    assert sent == [200]


def test_the_shutdown_sweep_pins_each_pid_to_its_start_time(monkeypatch):
    _fake_os(monkeypatch)
    monkeypatch.setattr(desktop.os, "getpid", lambda: 100)
    calls = []
    monkeypatch.setattr(proc, "_alive", lambda pid: pid in START)    # the fake OS's live set
    monkeypatch.setattr(proc, "kill_tree", lambda pid, **kw: calls.append((pid, kw)))
    server = types.SimpleNamespace(should_exit=False)
    thread = types.SimpleNamespace(join=lambda t: None)
    desktop.shutdown(server, thread)
    assert sorted(calls) == [(200, {"expect_start": 60}), (201, {"expect_start": 70})]


def test_windows_terminates_exactly_the_validated_tree(monkeypatch):
    """taskkill /T follows parent ids blindly; the tree is now ours."""
    import agent.platform.windows as win

    _fake_os(monkeypatch)
    killed = []
    monkeypatch.setattr(win, "_terminate", lambda pid, *a: killed.append(pid))
    win.kill_tree_signal(100, 5)
    assert killed[-1] == 100 and sorted(killed[:-1]) == [200, 201]
    assert killed.index(201) < killed.index(200), "leaves first"
    assert 300 not in killed and 301 not in killed


def test_alive_answers_from_the_kernel_correctly():
    """The fast alive() must still be RIGHT: running, exited, never existed."""
    import os
    import subprocess
    import sys

    assert proc._alive(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
    p.wait(30)
    assert not proc._alive(p.pid) or p.pid == os.getpid()
    assert not proc._alive(4_000_000_000 % 2**31)       # far beyond any live pid


# Iteration 3, 1.2. Validating a pid and then signalling it by NUMBER leaves a
# window in which the pid can change hands. The identity check now happens
# through the same handle (Windows) or pidfd (Linux) that delivers the kill.

import sys  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows handle path")
def test_a_kill_pinned_to_another_start_time_signals_nothing():
    import subprocess

    import agent.platform.windows as win

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         stdin=subprocess.DEVNULL)
    try:
        real = proc.process_start_time(p.pid)
        assert real is not None
        win._terminate(p.pid, real + 1)          # someone else's process now
        assert p.poll() is None, "killed a process whose identity did not match"
        win._terminate(p.pid, real)
        assert p.wait(10) is not None
    finally:
        if p.poll() is None:
            proc.kill_tree(p.pid)


def test_posix_checks_identity_before_it_signals(monkeypatch):
    import agent.platform.posix as posix

    sent = []
    monkeypatch.setattr(posix.os, "kill", lambda pid, sig: sent.append(pid), raising=False)
    monkeypatch.delattr(posix.os, "pidfd_open", raising=False)
    monkeypatch.setattr(proc, "process_start_time", lambda pid: 99)
    posix._sigkill(4242, expect_start=98)
    assert sent == [], "signalled a pid whose start time did not match"
    posix._sigkill(4242, expect_start=99)
    assert sent == [4242]
