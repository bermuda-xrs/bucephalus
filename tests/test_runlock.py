from __future__ import annotations

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
        holder = int(line[1])
        with pytest.raises(RunLockBusy):
            RunLock(lockfile).acquire()

        if holder == proc.pid:
            pass
    finally:
        kill_tree(proc.pid)
        proc.kill()
        proc.wait(timeout=10)
        proc.stdout.close()

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            RunLock(lockfile, "next run").acquire().release()
            break
        except RunLockBusy:
            time.sleep(0.5)
    else:
        raise AssertionError("the lock outlived the process that held it")


def test_both_harnesses_share_one_lock_file():
    noise = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    run = (ROOT / "bench" / "run.py").read_text(encoding="utf-8")
    for src, name in ((noise, "noise.py"), (run, "run.py")):
        assert "RunLock(" in src, f"{name} does not take the measurement lock"
        assert "RunLock(measurement_lock_path(" in src, f"{name} uses a different lock file"


def test_an_unbound_lock_is_not_silently_released(tmp_path):
    import gc

    RunLock(tmp_path / "m.lock", "unbound").acquire()
    gc.collect()

    with pytest.raises(RunLockBusy):
        RunLock(tmp_path / "m.lock", "second").acquire()


def test_the_lockfile_names_exactly_one_pid(tmp_path):
    lf = tmp_path / "m.lock"
    for _ in range(3):
        RunLock(lf, "serial").acquire().release()
    body = lf.read_text(encoding="utf-8").strip()
    assert len(body.split()) == 1, f"lock file accumulated PIDs: {body!r}"
    assert body.isdigit()


def test_kill_tree_kills_the_child_not_just_the_parent(tmp_path):
    from agent.proc import _alive, kill_tree

    marker = tmp_path / "grandchild.pid"
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
        proc.stdout.close()


def test_kill_tree_on_a_dead_pid_is_success():
    from agent.proc import kill_tree

    assert kill_tree(999999) is True
    assert kill_tree(-1) is True


def test_free_memory_is_readable():
    from agent.proc import free_memory_gb

    gb = free_memory_gb()
    assert gb is None or 0.0 < gb < 4096.0


def test_reap_orphans_is_safe_when_there_are_none():
    from agent.proc import reap_orphans

    assert isinstance(reap_orphans(("definitely-not-a-real-process",)), list)
