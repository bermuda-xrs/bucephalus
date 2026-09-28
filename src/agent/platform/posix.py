"""POSIX - Linux, and the base that darwin.py builds on. RAN IN WSL2 ONLY.

First run 2026-09-25 in WSL2 (Debian 13, Python 3.13): the seven real-kernel
properties held, and the run found D54-D59 (docs/evidence/wsl-2026-09-25).
Bare-metal Linux has not run it. docs/PLATFORMS.md, section 5, is the checklist, and tests/test_posix_platform.py holds the
properties (its POSIX half skips everywhere else).

Five defects were found here by reading, before any of it had run, and are
fixed below (2026-09-25):

  1. kill_tree killed its CALLER. It signalled the target's process group,
     and nothing gave a sandbox child a group of its own - so the group was
     the caller's. Every child now leads its own session, and a pid that
     shares the caller's group is killed by parentage, never by killpg.
  2. No process table on macOS - darwin.py.
  3. Limits were set in a preexec_fn, which Python documents as unsafe in a
     threaded process, and the control plane is threaded. They are now set by
     a trampoline: a fresh single-threaded interpreter that calls setrlimit
     and then execs the real child, so no Python runs between fork and exec.
  4. RLIMIT_NPROC counts every process the USER owns, so on a desktop with
     more than 32 processes the sandbox could not start one. Dropped, and
     replaced by a watchdog counting the run's OWN process group - reactive,
     and the run record says so.
  5. RLIMIT_AS is not enforced on macOS - darwin.py's memory watchdog.

What the kernel enforces here: address space (RLIMIT_AS), CPU seconds, file
size. What a watchdog enforces, every WATCH_INTERVAL_S: the process count.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time

from . import LimitsRefused  # noqa: F401 - part of the layer's vocabulary

WATCH_INTERVAL_S = 0.1
_SIGKILL = getattr(signal, "SIGKILL", 9)   # imported on Windows by the portable tests
REFUSED_EXIT = 126


# Runs in a fresh interpreter, alone, before the real child exists: set the
# limits, then become the child. A refused limit ends it here - fail closed,
# the child never runs.
LIMITER = """\
import json, os, resource, sys
L = json.loads(sys.argv[1])
try:
    if "as" in L:
        resource.setrlimit(resource.RLIMIT_AS, (L["as"], L["as"]))
    resource.setrlimit(resource.RLIMIT_CPU, (L["cpu"], L["cpu"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (L["fsize"], L["fsize"]))
except (ValueError, OSError) as e:
    sys.stderr.write("sandbox: resource limits could not be applied, so the "
                     "code was not run: %s\\n" % (e,))
    sys.stderr.flush()
    os._exit(126)
os.execv(sys.argv[2], sys.argv[2:])
"""


# ---------------------------------------------------------------- processes

def _same(pid: int, expect_start: int | None) -> bool:
    from .. import proc

    return expect_start is None or proc.process_start_time(pid) == expect_start


def _sigkill(pid: int, expect_start: int | None = None, group: int | None = None) -> None:
    """SIGKILL `pid` (or its group) - if it is still the process that started
    at `expect_start` (iteration 3, 1.2). Where the kernel has pidfds the
    check is made on the process the pidfd pins, and the signal goes through
    it, so the pid cannot change hands in between. macOS has none: there it
    is check-then-signal, a window of microseconds, stated rather than hidden.
    A group kill cannot go through a pidfd; it is sent only after `pid`'s
    identity is confirmed through one. Raises OSError if the group signal
    fails, so the caller can fall back to parentage."""
    fd = None
    try:
        fd = os.pidfd_open(pid)
    except (AttributeError, OSError):
        fd = None
    try:
        if not _same(pid, expect_start):
            return
        if group is not None:
            os.killpg(group, _SIGKILL)
            return
        try:
            if fd is not None:
                signal.pidfd_send_signal(fd, _SIGKILL)
            else:
                os.kill(pid, _SIGKILL)
        except OSError:
            pass
    finally:
        if fd is not None:
            os.close(fd)


def kill_tree_signal(pid: int, timeout_s: float, expect_start: int | None = None) -> None:
    """Kill pid and everything it started - never the caller.

    A sandbox child leads its own session (spawn_limited), so its group is
    exactly its tree and killpg is precise. A pid in the CALLER's group was
    not started that way; signalling that group is how this used to kill the
    agent itself. It is killed by parentage instead, leaves first, each pinned
    to the start time seen when the tree was listed.
    """
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return
    if pgid != os.getpgid(0):
        try:
            _sigkill(pid, expect_start, group=pgid)
            return
        except OSError:
            pass
    from ..proc import descendants, process_start_time   # start-time validated (D42)

    kids = descendants(pid)
    starts = {p: process_start_time(p) for p in kids}
    for p in reversed(kids):
        if starts[p] is not None:
            _sigkill(p, starts[p])
    _sigkill(pid, expect_start)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_table() -> list[tuple[int, int, str]]:
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


def group_members(pgid: int) -> list[int]:
    """Every pid in process group `pgid` - a sandbox run's whole tree, the
    orphaned-and-reparented included, which parentage alone would miss."""
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8") as fh:
                raw = fh.read()
            if int(raw[raw.rindex(")") + 2:].split()[2]) == pgid:
                out.append(int(entry))
        except (OSError, ValueError, IndexError):
            continue
    return out


def process_start_time(pid: int) -> int | None:
    with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
        raw = fh.read()
    return int(raw[raw.rindex(")") + 2:].split()[19])


def process_memory_bytes(pid: int, field: str = "WorkingSetSize") -> int | None:
    if field != "WorkingSetSize":
        raise NotImplementedError("D1: private commit is Windows-only here")
    with open(f"/proc/{pid}/statm", encoding="utf-8") as fh:
        return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def memory_status() -> dict:
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


def free_memory_gb() -> float | None:
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 ** 2
    return None


# ---------------------------------------------------------------- limits

def limits_note(timeout_s: int, memory_bytes: int, max_processes: int) -> str:
    return (f"rlimits (kernel-enforced): address space {memory_bytes // 2**20} MB, "
            f"CPU {timeout_s} s, file size 64 MB. Processes: at most "
            f"{max_processes} in this run's process group, enforced by a watchdog "
            f"every {WATCH_INTERVAL_S:g} s - REACTIVE, not kernel-enforced.")


def over_limits(n_processes: int, max_processes: int,
                footprint_bytes: int | None = None,
                memory_bytes: int | None = None) -> str | None:
    """The watchdog's decision, separated so it can be tested anywhere."""
    if n_processes > max_processes:
        return (f"process limit: {n_processes} processes in the run's group, "
                f"limit {max_processes} (watchdog, reactive)")
    if memory_bytes is not None and footprint_bytes is not None \
            and footprint_bytes > memory_bytes:
        return (f"memory limit: {footprint_bytes // 2**20} MB footprint, limit "
                f"{memory_bytes // 2**20} MB (watchdog, reactive - not "
                "kernel-enforced)")
    return None


def _watch(proc, max_processes: int, memory_bytes: int | None) -> None:
    from . import current   # darwin on a Mac: no /proc there

    while proc.poll() is None:
        try:
            members = current.group_members(proc.pid)
            footprint = None
            if memory_bytes is not None:
                footprint = sum(current.process_memory_bytes(p) or 0 for p in members)
            reason = over_limits(len(members), max_processes, footprint, memory_bytes)
        except Exception:  # noqa: BLE001 - a failed sample is not a verdict
            reason = None
        if reason:
            proc.alpha_limit = reason
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            return
        time.sleep(WATCH_INTERVAL_S)


def spawn_limited(argv: list[str], *, timeout_s: int, memory_bytes: int,
                  max_processes: int, fsize_bytes: int,
                  memory_by_rlimit: bool = True, **popen):
    """Start argv in its OWN session with the limits in place before its first
    instruction. Returns (proc, closer); closer kills the whole session.
    `memory_by_rlimit=False` (darwin) caps memory by watchdog instead."""
    from ..frozen import interpreter_argv

    limits = {"cpu": int(timeout_s), "fsize": int(fsize_bytes)}
    if memory_by_rlimit:
        limits["as"] = int(memory_bytes)
    wrapped = [*interpreter_argv(), "-c", LIMITER, json.dumps(limits), *argv]
    proc = subprocess.Popen(wrapped, start_new_session=True, **popen)  # noqa: S603
    proc.alpha_limit = None
    threading.Thread(
        target=_watch, args=(proc, max_processes,
                             None if memory_by_rlimit else memory_bytes),
        daemon=True, name=f"sandbox-watch-{proc.pid}").start()

    def close() -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    return proc, close


def exit_note(proc, timeout_s: int) -> str:
    if getattr(proc, "alpha_limit", None):
        return f"\nsandbox: {proc.alpha_limit}"
    if proc.returncode == -getattr(signal, "SIGXCPU", 24):
        return f"\nsandbox: CPU time limit reached ({timeout_s} s)"
    if proc.returncode == -getattr(signal, "SIGXFSZ", 25):
        return "\nsandbox: file size limit reached (64 MB)"
    return ""


# ---------------------------------------------------------------- file locks

def lock_file(fh) -> None:
    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock_file(fh) -> None:
    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
