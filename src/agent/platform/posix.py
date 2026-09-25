"""POSIX - Linux and macOS. NONE OF THIS HAS EVER RUN.

The pre-refactor code from agent/proc.py, agent/runlock.py and
agent/execute/sandbox.py, moved verbatim (2026-09-25). Read the known defects
in docs/PLATFORMS.md before trusting any of it: the first run on a Mac or a
Linux machine is a security audit, not a smoke test. In short -

  * kill_tree_signal kills the target's PROCESS GROUP, and nothing here puts
    a child in its own group: the group is the caller's, so this kills the
    caller.
  * process_table / process_start_time / memory read /proc. macOS has none,
    so there they return nothing and every reaper, the desktop's shutdown
    sweep included, sees no processes.
  * spawn_limited applies rlimits in a preexec_fn, which Python documents as
    unsafe in a threaded process - and the control plane is threaded.
  * RLIMIT_NPROC counts every process the USER owns, not the sandbox's.
  * RLIMIT_AS is not enforced on macOS, and a small value may be refused
    outright - so there the memory cap is absent, or every run fails closed.
"""

from __future__ import annotations

import os
import signal
import subprocess

from . import LimitsRefused


# ---------------------------------------------------------------- processes

def kill_tree_signal(pid: int, timeout_s: float) -> None:  # pragma: no cover
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (OSError, AttributeError):
        os.kill(pid, signal.SIGKILL)


def alive(pid: int) -> bool:  # pragma: no cover
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_table() -> list[tuple[int, int, str]]:  # pragma: no cover
    rows = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8") as fh:
                raw = fh.read()
            name = raw[raw.index("(") + 1:raw.rindex(")")]
            after = raw[raw.rindex(")") + 2:].split()
            rows.append((int(entry), int(after[1]), name))
        except (OSError, ValueError, IndexError):
            continue
    return rows


def process_start_time(pid: int) -> int | None:  # pragma: no cover
    with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
        raw = fh.read()
    return int(raw[raw.rindex(")") + 2:].split()[19])


def process_memory_bytes(pid: int, field: str = "WorkingSetSize") -> int | None:  # pragma: no cover
    if field != "WorkingSetSize":
        raise NotImplementedError("D1: private commit is Windows-only here")
    with open(f"/proc/{pid}/statm", encoding="utf-8") as fh:
        return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def memory_status() -> dict:  # pragma: no cover
    vals = {}
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            k, _, v = line.partition(":")
            vals[k] = int(v.split()[0]) * 1024
    phys = vals.get("MemTotal", 0)
    committed = vals.get("Committed_AS", 0)
    return {
        "available_gb": round(vals.get("MemAvailable", 0) / 1024 ** 3, 2),
        "committed_gb": round(committed / 1024 ** 3, 2),
        "physical_gb": round(phys / 1024 ** 3, 2),
        "commit_ratio": round(committed / phys, 3) if phys else None,
    }


def free_memory_gb() -> float | None:  # pragma: no cover
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 ** 2
    return None


# ---------------------------------------------------------------- limits

def limits_note(timeout_s: int, memory_bytes: int, max_processes: int) -> str:  # pragma: no cover
    return ""


def _posix_limits(timeout_s: int, memory_bytes: int, max_processes: int,
                  fsize_bytes: int):  # pragma: no cover - has never executed
    import resource

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (timeout_s, timeout_s))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))
        except (ValueError, OSError):
            pass
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_bytes,) * 2)

    return apply


def spawn_limited(argv: list[str], *, timeout_s: int, memory_bytes: int,
                  max_processes: int, fsize_bytes: int, **popen):  # pragma: no cover
    try:
        proc = subprocess.Popen(argv, preexec_fn=_posix_limits(  # noqa: S603, PLW1509
            timeout_s, memory_bytes, max_processes, fsize_bytes), **popen)
    except subprocess.SubprocessError as e:
        # A limit the kernel refused (macOS RLIMIT_AS, for one). It crashed
        # the caller before the refactor; now it is the refusal it always
        # meant - fail closed, the code does not run.
        raise LimitsRefused(str(e)) from e
    return proc, (lambda: None)


def cpu_limit_note(returncode, timeout_s: int) -> str:  # pragma: no cover
    return ""


# ---------------------------------------------------------------- file locks

def lock_file(fh) -> None:  # pragma: no cover
    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock_file(fh) -> None:  # pragma: no cover
    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
