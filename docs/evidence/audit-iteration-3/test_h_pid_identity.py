"""Audit probes: iteration 3's kill-identity change (proc.kill_tree,
platform/windows.kill_tree_signal). The only real process touched is a
sleeper this probe starts itself; everything else is a spy.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

import agent.proc as proc

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the Windows half")


def test_FINDING_an_unreadable_start_time_turns_the_pin_off(monkeypatch):
    """kill_tree reads `now = process_start_time(pid)` and passes it on as the
    pin. When it cannot be read (None: the process exited in between, or
    OpenProcess was refused), `_terminate(pid, None)` terminates whatever
    holds the pid with no identity check at all - the fail-open direction,
    where every other unknown in this code spares."""
    import agent.platform.windows as win

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    seen = []
    real_terminate = win._terminate
    monkeypatch.setattr(win, "_terminate", lambda pid, expect=None: (seen.append((pid, expect)),
                                                                     real_terminate(pid, expect)))
    monkeypatch.setattr(proc, "process_start_time", lambda pid: None)
    monkeypatch.setattr(proc, "descendants", lambda pid: [])
    try:
        proc.kill_tree(child.pid, timeout_s=5)
        print("terminate called with:", seen, "; child exit code:", child.wait(10))
        assert seen == [(child.pid, None)]
    finally:
        if child.poll() is None:
            monkeypatch.undo()
            proc.kill_tree(child.pid)


def test_FINDING_a_childs_pin_is_read_after_the_listing_not_at_it(monkeypatch):
    """The commit says "the start time seen at listing travels with each pid".
    It does not: `descendants()` returns bare pids, and kill_tree_signal reads
    each start time AGAIN afterwards, so a pid recycled between the two reads
    is pinned to the NEW process. Shown with spies: the listing saw start 111,
    the pid now answers 222, and 222 is what the kill is pinned to."""
    import agent.platform.windows as win

    starts = {"listing": 111, "now": 222}
    calls = []
    monkeypatch.setattr(proc, "descendants", lambda pid: [4242])       # listed, with 111
    monkeypatch.setattr(proc, "process_start_time",
                        lambda pid: starts["now"] if pid == 4242 else 999)
    monkeypatch.setattr(win, "_terminate", lambda pid, expect=None: calls.append((pid, expect)))
    win.kill_tree_signal(1234, 5.0, 999)
    print("kills:", calls)
    assert (4242, 222) in calls and (4242, 111) not in calls
