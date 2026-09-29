"""macOS. NEVER RUN ANYWHERE - docs/PLATFORMS.md section 5 is its audit.

posix.py serves Linux and most of this; what differs on a Mac is here:

  * NO /proc. The process table, start times and memory come from `ps`,
    `sysctl`, `vm_stat` and libproc (ctypes). posix.py's /proc versions
    returned nothing on a Mac, so the orphan reaper and the desktop's
    shutdown sweep saw no processes at all.
  * NO KERNEL MEMORY CAP. Darwin does not enforce RLIMIT_AS on the
    allocations that matter, and refuses values below a process's current
    virtual size - hundreds of GiB on arrival - so any real cap is either
    refused (every run fails closed) or ignored. So RLIMIT_AS is not set,
    and the memory cap is a WATCHDOG: every WATCH_INTERVAL_S it sums
    `ri_phys_footprint` (proc_pid_rusage, RUSAGE_INFO_V4 - the counter
    macOS's own memory-pressure system uses) over the run's process group
    and kills the group past the cap. REACTIVE: a child can overshoot by one
    interval's allocations before it dies. `limits_note()` says so in every
    run's record. If a hard cap is required, run the sandbox in a Linux VM.
    Sources: srelens#713, imandrakit#26, llm-cad#38 (PLATFORMS.md s.3).

Parsers are separate from the calls that feed them, so they are tested on
every platform; the calls are tested on a Mac only.
"""

from __future__ import annotations

import ctypes
import struct
import subprocess
import time

from . import posix
from .posix import (  # noqa: F401 - the rest of the contract, shared
    WATCH_INTERVAL_S, adopted, alive, exit_note, has_data_streams, kill_tree_signal,
    link_names, lock_file, over_limits, spawn_denied_processes, unlock_file)

RUSAGE_INFO_V4 = 4
# rusage_info_v4: 16-byte uuid, then u64s - user_time, system_time,
# pkg_idle_wkups, interrupt_wkups, pageins, wired_size, resident_size,
# phys_footprint. <sys/resource.h>.
FOOTPRINT_OFFSET = 16 + 7 * 8
_RUSAGE_BYTES = 16 + 64 * 8          # v4 is smaller; the excess is harmless


def _run(*argv: str) -> str:
    return subprocess.run(list(argv), capture_output=True, text=True,
                          timeout=10, check=True).stdout


# ---------------------------------------------------------------- parsers

def parse_ps_table(text: str) -> list[tuple[int, int, str]]:
    """`ps -axo pid=,ppid=,comm=`. comm is the executable path and may hold
    spaces (/Applications/Some App.app/...), so only two splits."""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        name = parts[2].rsplit("/", 1)[-1] if len(parts) == 3 else ""
        rows.append((pid, ppid, name))
    return rows


def parse_ps_groups(text: str, pgid: int) -> list[int]:
    """`ps -axo pid=,pgid=` -> the pids in `pgid`."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) == pgid:
            out.append(int(parts[0]))
    return out


def parse_lstart(text: str) -> int | None:
    """`ps -o lstart= -p PID`: 'Thu Sep 25 12:00:01 2026' -> epoch seconds.
    One-second resolution; the reaper compares a parent's with a child's, and
    a tie reads as 'parent not younger' - the sparing direction."""
    s = " ".join(text.split())
    if not s:
        return None
    return int(time.mktime(time.strptime(s, "%a %b %d %H:%M:%S %Y")))


def parse_vm_stat(text: str) -> dict[str, int]:
    """`vm_stat` -> {'page size': n, 'Pages free': n, ...}."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        if "page size of" in line:
            out["page size"] = int(line.split("page size of")[1].split()[0])
            continue
        k, _, v = line.partition(":")
        v = v.strip().rstrip(".")
        if v.isdigit():
            out[k.strip()] = int(v)
    return out


def available_bytes(vm: dict[str, int]) -> int:
    page = vm.get("page size", 4096)
    return page * sum(vm.get(k, 0) for k in
                      ("Pages free", "Pages inactive", "Pages speculative"))


def footprint_from_rusage(buf: bytes) -> int:
    return struct.unpack_from("<Q", buf, FOOTPRINT_OFFSET)[0]


# ---------------------------------------------------------------- processes

def process_table() -> list[tuple[int, int, str]]:
    return parse_ps_table(_run("ps", "-axo", "pid=,ppid=,comm="))


def group_members(pgid: int) -> list[int]:
    return parse_ps_groups(_run("ps", "-axo", "pid=,pgid="), pgid)


def process_start_time(pid: int) -> int | None:
    return parse_lstart(_run("ps", "-o", "lstart=", "-p", str(pid)))


def process_memory_bytes(pid: int, field: str = "WorkingSetSize") -> int | None:
    """phys_footprint - what Activity Monitor calls Memory."""
    if field != "WorkingSetSize":
        raise NotImplementedError("D1: private commit is Windows-only here")
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    buf = ctypes.create_string_buffer(_RUSAGE_BYTES)
    if lib.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(RUSAGE_INFO_V4), buf) != 0:
        return None
    return footprint_from_rusage(buf.raw)


def memory_status() -> dict:
    """No commit charge on Darwin, so no commit ratio: the preflight that
    keys off it reads None, which is 'unmeasured', not 'fine'."""
    phys = int(_run("sysctl", "-n", "hw.memsize").strip())
    avail = available_bytes(parse_vm_stat(_run("vm_stat")))
    return {"available_gb": round(avail / 1024 ** 3, 2), "committed_gb": None,
            "physical_gb": round(phys / 1024 ** 3, 2), "commit_ratio": None}


def free_memory_gb() -> float | None:
    return available_bytes(parse_vm_stat(_run("vm_stat"))) / 1024 ** 3


# ---------------------------------------------------------------- limits

def limits_note(timeout_s: int, memory_bytes: int, max_processes: int) -> str:
    return (f"rlimits (kernel-enforced): CPU {timeout_s} s, file size 64 MB. "
            f"Memory: {memory_bytes // 2**20} MB phys_footprint and at most "
            f"{max_processes} processes in this run's group, enforced by a "
            f"watchdog every {WATCH_INTERVAL_S:g} s - REACTIVE, NOT "
            "kernel-enforced: macOS does not enforce RLIMIT_AS.")


def spawn_limited(argv: list[str], **kw):
    """posix.spawn_limited without RLIMIT_AS, with the memory watchdog on."""
    return posix.spawn_limited(argv, memory_by_rlimit=False, **kw)
