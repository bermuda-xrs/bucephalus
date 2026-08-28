"""Killing a process must kill what it spawned.

Two instances of the same bug in two days, which makes it a class:

  * A venv `python.exe` on Windows is a TRAMPOLINE. `Popen` returns the
    wrapper's pid while the real interpreter runs as its child.
    `proc.kill()` left the interpreter alive, still holding a lock, and made
    `test_kill_and_resume`'s claim of "a real kill, no `finally` block runs"
    false — it passed on a marker file regardless.

  * Ollama spawns `llama-server` as a child. Killing Ollama left it holding
    5865 MB. A 15.5 GB machine went to 0.9 GB free and 24.4 GB committed, and
    one model call took 1499.8s for work that takes 88.1s unloaded — an 18x
    slowdown that silently rewrote a measurement whose cost axis is seconds.

So every termination goes through `kill_tree`, and every timed run reaps
known orphans first. The third instance of this bug is somewhere; this is
where it gets caught.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

# Processes that outlive their parent and hold real memory. Reaped before a
# timed run because their cost lands on the measurement, not on them.
ORPHAN_NAMES = ("llama-server",)


def kill_tree(pid: int, timeout_s: float = 10.0) -> bool:
    """Kill `pid` and every process it spawned. True if it is gone after.

    Best-effort and idempotent: killing an already-dead pid is success, not
    an error, because the caller's intent is "make sure this is not running".
    """
    if pid <= 0:
        return True
    try:
        if sys.platform == "win32":
            # /T is the whole point: without it the child survives.
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=timeout_s)
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (OSError, AttributeError):
                os.kill(pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001 - already gone is success
        pass

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    return not _alive(pid)


def _alive(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def find_by_name(name: str) -> list[int]:
    """Pids whose image name matches, so orphans can be found without psutil."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}.exe", "/NH"],
                capture_output=True, text=True, timeout=15).stdout
            pids = []
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].lower().startswith(name.lower()):
                    try:
                        pids.append(int(parts[1]))
                    except ValueError:
                        continue
            return pids
        out = subprocess.run(["pgrep", "-x", name], capture_output=True,
                             text=True, timeout=15).stdout
        return [int(x) for x in out.split()]
    except Exception:  # noqa: BLE001
        return []


def reap_orphans(names: tuple[str, ...] = ORPHAN_NAMES) -> list[int]:
    """Kill known orphaned workers. Returns the pids reaped.

    Called before a timed run: an orphan's memory is charged to whatever runs
    next, and on a 15.5 GB box that is the difference between 88s and 1500s
    for the same model call.
    """
    reaped = []
    for name in names:
        for pid in find_by_name(name):
            if kill_tree(pid):
                reaped.append(pid)
    return reaped


def free_memory_gb() -> float | None:
    """Physical memory free, or None if it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _MS()
            m.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullAvailPhys / 1024 ** 3
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 ** 2
    except Exception:  # noqa: BLE001 - a missing metric must not stop a run
        return None
    return None
