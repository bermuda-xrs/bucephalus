from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent.proc import (  # noqa: E402
    kill_tree,
    orphan_pids,
    process_start_time,
    process_table,
)

CHILD = """
import os, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
time.sleep(120)
"""

PARENT = """
import os, subprocess, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
subprocess.Popen([sys.executable, "-c", sys.argv[3], sys.argv[2]])
time.sleep(120)
"""


def _read_pid(path, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = path.read_text().strip()
            if raw:
                return int(raw)
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"helper never reported its pid: {path}")


def _kill_single(pid):
    os.kill(pid, 9)


def _wait_gone(pid, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(p == pid for p, _, _ in process_table()):
            return True
        time.sleep(0.1)
    return False


def _ancestors(pid):
    table = {p: pp for p, pp, _ in process_table()}
    out, seen = [], {pid}
    cur = table.get(pid)
    while cur and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = table.get(cur)
    return out


def _wait_visible(pid, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(p == pid for p, _, _ in process_table()):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def family(tmp_path):
    ppath, cpath = tmp_path / "parent.pid", tmp_path / "child.pid"
    proc = subprocess.Popen(
        [sys.executable, "-c", PARENT, str(ppath), str(cpath), CHILD])
    try:
        parent_pid = _read_pid(ppath)
        child_pid = _read_pid(cpath)
        assert _wait_visible(child_pid), "child not visible in the process table"
        yield parent_pid, child_pid
    finally:
        for pid in (locals().get("child_pid", 0), locals().get("parent_pid", 0)):
            if pid:
                kill_tree(pid)
        kill_tree(proc.pid)
        proc.wait(timeout=15)


def test_a_healthy_child_is_not_selected(family):
    parent_pid, child_pid = family
    selected = orphan_pids(names=("python",))
    assert child_pid not in selected, (
        "live child of a live parent was selected for reaping - this is the "
        "bug that killed the warm model between ladder runs")


def test_a_genuine_orphan_is_selected(family):
    parent_pid, child_pid = family

    _kill_single(parent_pid)
    assert _wait_gone(parent_pid), "parent did not die"

    assert any(p == child_pid for p, _, _ in process_table()), (
        "child died with its parent; nothing was orphaned to detect")

    selected = set(orphan_pids(names=("python",)))
    branch = {child_pid} | set(_ancestors(child_pid))
    assert selected & branch, (
        "nothing in the abandoned branch was selected; the orphan went "
        f"undetected. selected={sorted(selected)} branch={sorted(branch)}")


def test_protect_pids_is_honoured(family):
    parent_pid, child_pid = family
    _kill_single(parent_pid)
    _wait_gone(parent_pid)

    selected = orphan_pids(names=("python",))
    assert selected, "precondition: something should be orphaned by now"
    victim = selected[0]

    assert victim not in orphan_pids(names=("python",),
                                     protect_pids=(victim,))


def test_reap_kills_exactly_what_selection_returns(family, monkeypatch):
    import agent.proc as proc

    killed: list[int] = []
    monkeypatch.setattr(proc, "kill_tree", lambda pid, **kw: killed.append(pid) or True)
    starts = {pid: proc.process_start_time(pid) for pid, _pp, _n in proc.process_table()}
    table = [row for row in proc.process_table() if starts.get(row[0]) is not None]
    monkeypatch.setattr(proc, "process_table", lambda: table)
    monkeypatch.setattr(proc, "process_start_time", lambda pid: starts.get(pid))

    expected = proc.orphan_pids(names=("python",))
    reaped = proc.reap_orphans(names=("python",))

    assert killed == expected
    assert reaped == expected


def test_selection_survives_an_unreadable_process_table(monkeypatch):
    import agent.proc as proc

    monkeypatch.setattr(proc, "process_table", lambda: [])
    assert proc.orphan_pids(names=("python",)) == []


def test_start_time_is_readable_for_our_own_process():
    assert process_start_time(os.getpid()) is not None


def test_a_recycled_parent_pid_does_not_shield_an_orphan(monkeypatch):
    import agent.proc as proc

    monkeypatch.setattr(proc, "process_table",
                        lambda: [(4242, 99, "llama-server.exe"),
                                 (99, 1, "unrelated.exe")])
    monkeypatch.setattr(proc, "process_start_time",
                        lambda pid: {4242: 100, 99: 500}[pid])

    assert proc.orphan_pids(names=("llama-server",)) == [4242]


def test_a_real_parent_shields_its_child(monkeypatch):
    import agent.proc as proc

    monkeypatch.setattr(proc, "process_table",
                        lambda: [(4242, 99, "llama-server.exe"),
                                 (99, 1, "ollama.exe")])
    monkeypatch.setattr(proc, "process_start_time",
                        lambda pid: {4242: 500, 99: 100}[pid])

    assert proc.orphan_pids(names=("llama-server",)) == []


DAEMON_PARENT = """
import os, subprocess, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
subprocess.Popen([sys.executable, "-c", sys.argv[3], sys.argv[2]], start_new_session=True)
time.sleep(120)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX re-parenting")
def test_a_daemon_in_its_own_session_is_never_selected(tmp_path):
    ppath, cpath = tmp_path / "parent.pid", tmp_path / "child.pid"
    proc = subprocess.Popen([sys.executable, "-c", DAEMON_PARENT, str(ppath), str(cpath), CHILD])
    parent_pid = child_pid = 0
    try:
        parent_pid = _read_pid(ppath)
        child_pid = _read_pid(cpath)
        assert _wait_visible(child_pid)
        _kill_single(parent_pid)
        proc.wait(10)
        assert _wait_gone(parent_pid), "parent did not die"
        assert child_pid not in orphan_pids(names=("python",))
    finally:
        for pid in (child_pid, parent_pid, proc.pid):
            if pid:
                kill_tree(pid)
