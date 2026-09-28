"""The reaper must kill orphans and spare live children.

The fourth bug in the process class, and the mirror of the first three.
`reap_orphans` used to match on process NAME alone, so it could not tell a
live `llama-server` owned by a running Ollama from one whose parent had died.
It killed the warm model three times in one measurement session, defeating
`keep_alive`, and because R0 runs first in a ladder that put a ~58 s cold
load on the first sample of every run - inflating the wall-clock drop the
harness-tax finding cites as evidence.

These tests pin the decision, not the kill. `kill_tree` was never the broken
part; choosing what to hand it was.

Note on pids: a venv `python.exe` on Windows is a trampoline (SPEC-DELTA
C13), so `Popen.pid` is the wrapper, not the interpreter. Every helper here
reports `os.getpid()` into a file and the tests use that, never `Popen.pid`.
"""

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
    """Kill ONE process, leaving its children alive - the opposite of kill_tree.

    `kill_tree` passes taskkill /T, which takes the whole tree. To create a
    genuine orphan the parent has to die on its own.
    """
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=15)
    else:
        os.kill(pid, 9)


def _wait_gone(pid, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(p == pid for p, _, _ in process_table()):
            return True
        time.sleep(0.1)
    return False


def _ancestors(pid):
    """Every pid above `pid`, nearest first. Stops at the root or a cycle."""
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
    """A live parent python with a live child python. Cleaned up either way."""
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


def test_a_healthy_child_is_not_selected(family):
    """The regression. A child whose parent is alive must be left alone."""
    parent_pid, child_pid = family
    selected = orphan_pids(names=("python",))
    assert child_pid not in selected, (
        "live child of a live parent was selected for reaping - this is the "
        "bug that killed the warm model between ladder runs")


def test_a_genuine_orphan_is_selected(family):
    """Kill only the parent; the abandoned branch must be recognised.

    The assertion is about the abandoned BRANCH, not the leaf, and that is
    not a weakening - it is the trampoline again (SPEC-DELTA C13). Launching
    the helper through the venv `python.exe` gives

        parent P  ->  wrapper W  ->  leaf C

    so killing P orphans W, while C still has W as a living parent and is
    correctly not an orphan. `kill_tree` then takes C with W, which is the
    behaviour wanted in production too: an abandoned `llama-server` tree
    should go as a tree.

    Asserting `child_pid in selected` would therefore be asserting the wrong
    thing, and it failed for that reason before this comment existed.
    """
    parent_pid, child_pid = family

    # NOT kill_tree: /T would take the child with it, and then there is
    # nothing orphaned left to detect.
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
    """An explicitly protected pid is never selected, orphan or not."""
    parent_pid, child_pid = family
    _kill_single(parent_pid)
    _wait_gone(parent_pid)

    selected = orphan_pids(names=("python",))
    assert selected, "precondition: something should be orphaned by now"
    victim = selected[0]

    assert victim not in orphan_pids(names=("python",),
                                     protect_pids=(victim,))


def test_reap_kills_exactly_what_selection_returns(family, monkeypatch):
    """`reap_orphans` must hand `kill_tree` the selection and nothing else."""
    import agent.proc as proc

    killed: list[int] = []
    monkeypatch.setattr(proc, "kill_tree", lambda pid, **kw: killed.append(pid) or True)
    # One frozen view of the machine for both calls. The live table changes
    # between them under a parallel suite - other workers start and orphan
    # python processes - and that race is not what this test is about
    # (iteration 3, 2.2: failed once under load, passed alone).
    table = proc.process_table()
    starts = {pid: proc.process_start_time(pid) for pid, _pp, _n in table}
    monkeypatch.setattr(proc, "process_table", lambda: table)
    monkeypatch.setattr(proc, "process_start_time", lambda pid: starts.get(pid))

    expected = proc.orphan_pids(names=("python",))
    reaped = proc.reap_orphans(names=("python",))

    assert killed == expected
    assert reaped == expected


def test_selection_survives_an_unreadable_process_table(monkeypatch):
    """No process table means no reaping, not indiscriminate reaping."""
    import agent.proc as proc

    monkeypatch.setattr(proc, "process_table", lambda: [])
    assert proc.orphan_pids(names=("python",)) == []


def test_start_time_is_readable_for_our_own_process():
    """The recycled-pid guard is only meaningful if creation times resolve."""
    assert process_start_time(os.getpid()) is not None


def test_a_recycled_parent_pid_does_not_shield_an_orphan(monkeypatch):
    """A 'parent' that started AFTER its child is a reused pid, not a parent.

    Without this, an orphan whose dead parent's pid gets reused would be
    spared forever - the exact 5.9 GB `llama-server` case the reaper exists
    to catch.
    """
    import agent.proc as proc

    monkeypatch.setattr(proc, "process_table",
                        lambda: [(4242, 99, "llama-server.exe"),
                                 (99, 1, "unrelated.exe")])
    # parent stamp is LATER than the child's -> pid was recycled
    monkeypatch.setattr(proc, "process_start_time",
                        lambda pid: {4242: 100, 99: 500}[pid])

    assert proc.orphan_pids(names=("llama-server",)) == [4242]


def test_a_real_parent_shields_its_child(monkeypatch):
    """The same shape, but the parent started first: healthy, leave it."""
    import agent.proc as proc

    monkeypatch.setattr(proc, "process_table",
                        lambda: [(4242, 99, "llama-server.exe"),
                                 (99, 1, "ollama.exe")])
    monkeypatch.setattr(proc, "process_start_time",
                        lambda pid: {4242: 500, 99: 100}[pid])

    assert proc.orphan_pids(names=("llama-server",)) == []
