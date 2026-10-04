"""D37 - the sandbox's limits on Windows are the kernel's, not advice.

Until 2026-09-15 a Windows child got no memory, CPU or process limit at all:
advisory path confinement plus a wall-clock timeout, under a scheduler and a
channel that run code unattended. These run for real on this machine; the
POSIX half is `resource` and has run only in WSL2 (2026-09-25).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the Windows half of D37")

if os.name == "nt":
    from agent.execute.sandbox import MEM_LIMIT_BYTES, run_sandboxed
    from agent.execute.winjob import (
        CPU_LIMIT_EXIT, JobError, JobLimits, close_job, spawn_in_job)

# D13: an interpreter in a frozen build too (agent.frozen).
from agent.frozen import interpreter_argv  # noqa: E402

PY = interpreter_argv()
SMALL = dict(memory_bytes=256 * 2**20, cpu_seconds=30, max_processes=8)
PIPES = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
             stderr=subprocess.PIPE, text=True)


def _run(code, timeout=30, **limits):
    proc, job = spawn_in_job([*PY, "-c", code],
                             JobLimits(**{**SMALL, **limits}), **PIPES)
    try:
        out, err = proc.communicate(timeout=timeout)
    finally:
        close_job(job)
    return proc.returncode, out, err


def test_run_code_past_the_memory_limit_fails():
    """Through the real entry point, at the real limit."""
    r = run_sandboxed(f"b = bytearray({MEM_LIMIT_BYTES + 128 * 2**20})\nprint('allocated')",
                      timeout_s=60)
    assert "allocated" not in r["stdout"], r
    assert "MemoryError" in r["stderr"], r
    assert any("job object" in n for n in r["notes"]), r["notes"]
    assert any("file size" in n for n in r["notes"]), "the missing limit must be named"


def test_memory_under_the_limit_is_untouched():
    rc, out, _ = _run("b = bytearray(64 * 2**20); print('allocated')")
    assert rc == 0 and "allocated" in out


def test_cpu_time_past_the_limit_is_killed():
    t0 = time.time()
    rc, out, _ = _run("while True: pass", timeout=60, cpu_seconds=1)
    assert rc == CPU_LIMIT_EXIT, hex(rc & 0xFFFFFFFF)
    assert time.time() - t0 < 50, "the wall clock stopped it, not the CPU limit"


def test_starting_processes_past_the_limit_is_refused():
    code = ("import subprocess, sys\nps = []\n"
            "for i in range(6):\n"
            "    try:\n"
            "        ps.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)']))\n"
            "    except OSError as e:\n"
            "        print('refused', getattr(e, 'winerror', None)); break\n"
            "else:\n    print('all started')\n"
            "[p.wait() for p in ps]")
    rc, out, _ = _run(code, max_processes=4)
    assert "refused 1816" in out, out


def test_the_code_runs_inside_the_job_trampoline_included():
    """The venv launcher starts the real interpreter as a child. Created
    suspended, assigned, then resumed, so that child is born in the job."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_int)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    proc, job = spawn_in_job(
        [*PY, "-c", "import os, time; print(os.getpid(), flush=True); time.sleep(3)"],
        JobLimits(**SMALL), **PIPES)
    try:
        pid = int(proc.stdout.readline())
        h = k32.OpenProcess(0x1000, False, pid)
        inside = ctypes.c_int()
        assert k32.IsProcessInJob(h, job, ctypes.byref(inside))
        k32.CloseHandle(h)
        assert inside.value, f"pid {pid} (launcher {proc.pid}) runs outside the job"
    finally:
        proc.kill()
        close_job(job)
        proc.communicate(timeout=15)


def test_the_child_is_started_suspended(monkeypatch):
    """The trampoline test above can pass by luck. If the job is assigned
    before the launcher gets round to starting the interpreter, the child lands
    in the job even without CREATE_SUSPENDED - measured 2026-09-15: removing the
    flag left that test green. So the flag itself is pinned: it is what makes
    the ordering a guarantee rather than a race."""
    import agent.execute.winjob as winjob

    seen: list[int] = []
    real = subprocess.Popen

    def spy(*a, **k):
        seen.append(k.get("creationflags", 0))
        return real(*a, **k)

    monkeypatch.setattr(winjob.subprocess, "Popen", spy)
    proc, job = spawn_in_job([*PY, "-c", "print('ok')"],
                             JobLimits(**SMALL), **PIPES)
    try:
        out, _ = proc.communicate(timeout=30)
    finally:
        close_job(job)
    assert "ok" in out
    assert seen and seen[0] & winjob.CREATE_SUSPENDED, "started running, not suspended"


def test_closing_the_job_kills_what_it_started():
    proc, job = spawn_in_job([*PY, "-c", "import time; time.sleep(60)"],
                             JobLimits(**SMALL), **PIPES)
    time.sleep(0.5)
    close_job(job)
    proc.communicate(timeout=5)


def test_a_job_that_cannot_be_applied_refuses_the_run(monkeypatch):
    """Fail closed: no limits, no run."""
    import agent.execute.winjob as winjob

    real = winjob._api  # noqa: SLF001

    def broken():
        k32, ntdll = real()

        class K:
            def __getattr__(self, name):
                return getattr(k32, name)

            @staticmethod
            def AssignProcessToJobObject(job, handle):  # noqa: N802
                return 0

        return K(), ntdll

    monkeypatch.setattr(winjob, "_api", broken)
    import gc
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        with pytest.raises(JobError, match="never ran"):
            spawn_in_job([*PY, "-c", "print('ran')"], JobLimits(**SMALL), **PIPES)
        r = run_sandboxed("print('ran')", timeout_s=30)
        gc.collect()
    assert "ran" not in r["stdout"] and r["returncode"] == -1, r
    assert "limits could not be applied" in r["stderr"], r
    # The refused child's pipes are closed with it, not left to the collector.
    assert not [w for w in caught if issubclass(w.category, ResourceWarning)]


def test_agent_code_cannot_start_a_process_below_the_audit_hook(tmp_path):
    """D49 (audit H2). Spawning is refused to agent code by the audit hook,
    and `_winapi.CreateProcess` - the primitive under `subprocess` - raises
    no audit event: agent code started cmd.exe with it (confirmed 2026-09-30).
    For agent code the Job Object holds exactly the interpreter's own
    processes, so the kernel refuses a spawn by any route, ctypes included."""
    code = ("import _winapi\n"
            "try:\n"
            "    h = _winapi.CreateProcess(None, 'cmd.exe /c exit 7', None, None,"
            " False, 0, None, None, None)\n"
            "    print('SPAWNED')\n"
            "except OSError as e:\n"
            "    print('REFUSED', e.winerror)\n")
    guarded = tmp_path / "graders"
    guarded.mkdir()
    r = run_sandboxed(code, allowed_paths=[], timeout_s=30, protected_paths=[str(guarded)])
    assert "SPAWNED" not in r["stdout"] and "REFUSED" in r["stdout"], r


def test_harness_code_can_still_start_processes(tmp_path):
    """The other half: a harness run (a grader running pytest) spawns, and
    the tight limit is for agent code only."""
    code = ("import subprocess, sys\n"
            "print(subprocess.run([sys.executable, '-c', 'print(42)'],"
            " capture_output=True, text=True).stdout.strip())\n")
    r = run_sandboxed(code, allowed_paths=[], timeout_s=60)
    assert r["stdout"].strip().endswith("42"), r


def test_3B_L1_the_run_record_names_the_limit_that_was_applied(tmp_path):
    """Agent code's process cap is the interpreter's own count (D49); the note
    on the run's receipt said 32, the constant, whatever was applied."""
    from agent.platform import current

    guarded = tmp_path / "graders"
    guarded.mkdir()
    n = current.spawn_denied_processes()
    r = run_sandboxed("print(1)", allowed_paths=[], timeout_s=30, protected_paths=[str(guarded)])
    assert any(f"{n} processes" in note for note in r["notes"]), r["notes"]
    r = run_sandboxed("print(1)", allowed_paths=[], timeout_s=30)
    assert any("32 processes" in note for note in r["notes"]), r["notes"]
