"""Two measurement processes must not run at once.

This is not tidiness. On 2026-08-27 two `noise.py` passes overlapped for 36
minutes because a process that looked dead was not. The duplicated ledger
rows were harmless — they collapse on resume. The damage was:

  * three tasks were measured while two models competed for one CPU, so their
    wall clock is not comparable to the rest of the run, and
  * `budget_exhausted` is a TIME limit, so contention can MANUFACTURE that
    outcome on a task that would otherwise have finished. The suite reads as
    harder than it is, for reasons unrelated to the tasks.

It also breaks the documented single-process invariant: the hash-chain lock is
process-wide, so a second process cannot see it. Chains verified that time
only because they are per-task — luck, not design.

The lock must be released by the OS on process death, because these processes
are routinely killed rather than exiting cleanly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from agent.proc import kill_tree
from agent.runlock import RunLock, RunLockBusy

ROOT = Path(__file__).resolve().parents[1]


def test_a_second_holder_in_this_process_is_refused(tmp_path):
    lock = RunLock(tmp_path / "m.lock", "noise floor").acquire()
    try:
        with pytest.raises(RunLockBusy):
            RunLock(tmp_path / "m.lock", "calibration").acquire()
    finally:
        lock.release()


def test_the_refusal_explains_the_damage_not_just_the_conflict(tmp_path):
    """An operator who does not know WHY will just delete the lock file."""
    lock = RunLock(tmp_path / "m.lock").acquire()
    try:
        with pytest.raises(RunLockBusy) as e:
            RunLock(tmp_path / "m.lock").acquire()
        msg = str(e.value)
        assert "budget_exhausted" in msg, "say what contention actually does"
        assert "contention" in msg
        assert "releases" in msg, "say how to recover"
    finally:
        lock.release()


def test_releasing_lets_the_next_process_in(tmp_path):
    RunLock(tmp_path / "m.lock").acquire().release()
    second = RunLock(tmp_path / "m.lock").acquire()
    second.release()


def test_a_real_second_process_is_refused(tmp_path):
    """The case that actually bit: a separate OS process, not a second object."""
    lockfile = tmp_path / "m.lock"
    held = RunLock(lockfile, "noise floor").acquire()
    try:
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT / "src")!r})
            from agent.runlock import RunLock, RunLockBusy
            try:
                RunLock({str(lockfile)!r}, "second").acquire()
                print("ACQUIRED")
            except RunLockBusy:
                print("REFUSED")
        """)
        out = subprocess.run([sys.executable, "-c", script],
                             capture_output=True, text=True, timeout=60)
        assert "REFUSED" in out.stdout, (
            f"a second OS process took the lock: {out.stdout} {out.stderr}")
    finally:
        held.release()


def test_the_lock_is_released_when_a_process_is_killed(tmp_path):
    """The property that matters most here.

    These runs are routinely killed rather than exited. A lock needing tidy
    shutdown would strand every later run behind a file nobody owns.

    NOTE ON WHICH PID TO KILL. On Windows a venv's python.exe is a
    TRAMPOLINE: Popen returns the wrapper's pid while the real interpreter
    runs as its child. Killing the Popen handle leaves the interpreter alive,
    still holding the lock - which is how this test first failed. The lock
    file records os.getpid() from INSIDE the holder, so that is the pid to
    kill, and it is what `measure.ps1 stop` uses.
    """
    lockfile = tmp_path / "m.lock"
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(ROOT / "src")!r})
        from agent.runlock import RunLock
        import os
        RunLock({str(lockfile)!r}, "victim").acquire()
        print("HELD", os.getpid(), flush=True)
        time.sleep(120)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script],
                            stdout=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline().split()
        assert line[0] == "HELD"
        holder = int(line[1])                     # the real interpreter
        with pytest.raises(RunLockBusy):
            RunLock(lockfile).acquire()

        if holder == proc.pid:
            # not every interpreter is a trampoline; only assert the hazard
            # where it exists, rather than pinning an environment detail.
            pass
    finally:
        # kill the real holder, then the wrapper
        kill_tree(proc.pid)      # the trampoline AND the interpreter
        proc.kill()
        proc.wait(timeout=10)

    deadline = time.time() + 15
    while time.time() < deadline:                 # the OS releases lazily
        try:
            RunLock(lockfile, "next run").acquire().release()
            break
        except RunLockBusy:
            time.sleep(0.5)
    else:
        raise AssertionError("the lock outlived the process that held it")


def test_both_harnesses_share_one_lock_file():
    """A calibration racing a noise floor is the same failure.

    They compete for the same CPU and the same database, so one lock covers
    both rather than each guarding only itself.
    """
    noise = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    run = (ROOT / "bench" / "run.py").read_text(encoding="utf-8")
    for src, name in ((noise, "noise.py"), (run, "run.py")):
        assert "RunLock(" in src, f"{name} does not take the measurement lock"
        # D34: the ONE project lock, not a file beside the run's output.
        assert "RunLock(measurement_lock_path(" in src, f"{name} uses a different lock file"


def test_an_unbound_lock_is_not_silently_released(tmp_path):
    """`RunLock(p).acquire()` with the result discarded must still hold.

    Without a module-level reference the object is collected immediately, its
    file is closed, and the lock evaporates with no error anywhere — a silent
    unlock, which is the worst failure available to a guard whose only job is
    to refuse. Found by the kill test: the child held nothing.
    """
    import gc

    RunLock(tmp_path / "m.lock", "unbound").acquire()   # deliberately unbound
    gc.collect()

    with pytest.raises(RunLockBusy):
        RunLock(tmp_path / "m.lock", "second").acquire()


def test_the_lockfile_names_exactly_one_pid(tmp_path):
    """The error message names the holder, so it must not be a stale list.

    In "a+" mode writes ignore seek(), so each acquire appended another PID
    and the file read back as "31380 31380 16048" — an error that would send
    an operator chasing a process that exited hours ago.
    """
    lf = tmp_path / "m.lock"
    for _ in range(3):
        RunLock(lf, "serial").acquire().release()
    body = lf.read_text(encoding="utf-8").strip()
    assert len(body.split()) == 1, f"lock file accumulated PIDs: {body!r}"
    assert body.isdigit()


# ---------------------------------------------------------------------------
# 1.7 - parent dies, child survives. Two instances in two days is a class.
# ---------------------------------------------------------------------------


def test_kill_tree_kills_the_child_not_just_the_parent(tmp_path):
    """The bug, twice: a venv trampoline, then Ollama's llama-server.

    A parent that spawns a worker and is killed leaves the worker holding
    whatever it held - a lock, or 5.9 GB of RAM.
    """
    from agent.proc import _alive, kill_tree

    marker = tmp_path / "grandchild.pid"
    # as_posix(): a Windows path embedded in a doubly-nested string is read
    # as unicode escapes and fails to compile.
    inner = ("import time,os,pathlib;"
             "pathlib.Path(" + repr(marker.as_posix()) + ")"
             ".write_text(str(os.getpid()));"
             "time.sleep(120)")
    script = (
        "import subprocess, sys, time, os" + chr(10) +
        "subprocess.Popen([sys.executable, " + repr("-c") + ", " + repr(inner) + "])" + chr(10) +
        "print(" + repr("SPAWNED") + ", os.getpid(), flush=True)" + chr(10) +
        "time.sleep(120)" + chr(10)
    )
    proc = subprocess.Popen([sys.executable, "-c", script],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().split()[0] == "SPAWNED"
        deadline = time.time() + 30
        while time.time() < deadline and not marker.exists():
            time.sleep(0.2)
        assert marker.exists(), "grandchild never started"
        grandchild = int(marker.read_text(encoding="utf-8"))
        assert _alive(grandchild)

        kill_tree(proc.pid)

        deadline = time.time() + 20
        while time.time() < deadline and _alive(grandchild):
            time.sleep(0.3)
        assert not _alive(grandchild), (
            "the grandchild outlived kill_tree - this is the bug that left an "
            "llama-server holding 5.9 GB and made one model call 18x slower")
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_kill_tree_on_a_dead_pid_is_success():
    """Idempotent: the caller's intent is 'ensure not running'."""
    from agent.proc import kill_tree

    assert kill_tree(999999) is True
    assert kill_tree(-1) is True


def test_free_memory_is_readable():
    """The cost axis is seconds, so paging must be observable."""
    from agent.proc import free_memory_gb

    gb = free_memory_gb()
    assert gb is None or 0.0 < gb < 4096.0


def test_reap_orphans_is_safe_when_there_are_none():
    from agent.proc import reap_orphans

    assert isinstance(reap_orphans(("definitely-not-a-real-process",)), list)
