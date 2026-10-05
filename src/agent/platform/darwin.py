from __future__ import annotations

import ctypes
import struct
import subprocess
import time

from . import posix
from .posix import (  # noqa: F401
    WATCH_INTERVAL_S, adopted, alive, exit_note, has_data_streams, kill_tree_signal,
    link_names, lock_file, over_limits, spawn_denied_processes, unlock_file)

RUSAGE_INFO_V4 = 4
FOOTPRINT_OFFSET = 16 + 7 * 8
_RUSAGE_BYTES = 16 + 64 * 8


def _run(*argv: str) -> str:
    return subprocess.run(list(argv), capture_output=True, text=True,
                          timeout=10, check=True).stdout


def parse_ps_table(text: str) -> list[tuple[int, int, str]]:
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
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) == pgid:
            out.append(int(parts[0]))
    return out


def parse_lstart(text: str) -> int | None:
    s = " ".join(text.split())
    if not s:
        return None
    return int(time.mktime(time.strptime(s, "%a %b %d %H:%M:%S %Y")))


def parse_vm_stat(text: str) -> dict[str, int]:
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


def process_table() -> list[tuple[int, int, str]]:
    return parse_ps_table(_run("ps", "-axo", "pid=,ppid=,comm="))


def group_members(pgid: int) -> list[int]:
    return parse_ps_groups(_run("ps", "-axo", "pid=,pgid="), pgid)


def process_start_time(pid: int) -> int | None:
    return parse_lstart(_run("ps", "-o", "lstart=", "-p", str(pid)))


def process_memory_bytes(pid: int, field: str = "WorkingSetSize") -> int | None:
    if field != "WorkingSetSize":
        raise NotImplementedError("D1: private commit is Windows-only here")
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    buf = ctypes.create_string_buffer(_RUSAGE_BYTES)
    if lib.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(RUSAGE_INFO_V4), buf) != 0:
        return None
    return footprint_from_rusage(buf.raw)


def memory_status() -> dict:
    phys = int(_run("sysctl", "-n", "hw.memsize").strip())
    avail = available_bytes(parse_vm_stat(_run("vm_stat")))
    return {"available_gb": round(avail / 1024 ** 3, 2), "committed_gb": None,
            "physical_gb": round(phys / 1024 ** 3, 2), "commit_ratio": None}


def free_memory_gb() -> float | None:
    return available_bytes(parse_vm_stat(_run("vm_stat"))) / 1024 ** 3


def limits_note(timeout_s: int, memory_bytes: int, max_processes: int) -> str:
    return (f"rlimits (kernel-enforced): CPU {timeout_s} s, file size 64 MB. "
            f"Memory: {memory_bytes // 2**20} MB phys_footprint and at most "
            f"{max_processes} processes in this run's group, enforced by a "
            f"watchdog every {WATCH_INTERVAL_S:g} s - REACTIVE, NOT "
            "kernel-enforced: macOS does not enforce RLIMIT_AS.")


def spawn_limited(argv: list[str], **kw):
    return posix.spawn_limited(argv, memory_by_rlimit=False, **kw)
