from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time

from . import LimitsRefused  # noqa: F401

WATCH_INTERVAL_S = 0.1
_SIGKILL = getattr(signal, "SIGKILL", 9)
REFUSED_EXIT = 126


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


def _same(pid: int, expect_start: int | None) -> bool:
    from .. import proc

    return expect_start is None or proc.process_start_time(pid) == expect_start


def _sigkill(pid: int, expect_start: int | None = None, group: int | None = None) -> None:
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
    from ..proc import descendants, process_start_time

    kids = descendants(pid)
    starts = {p: process_start_time(p) for p in kids}
    for p in reversed(kids):
        if starts[p] is not None:
            _sigkill(p, starts[p])
    _sigkill(pid, expect_start)


def spawn_denied_processes() -> None:
    return None


def link_names(path: str) -> None:
    return None


def has_data_streams() -> bool:
    return False


def _stat_fields(pid: int) -> list[str] | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            raw = fh.read()
        return raw[raw.rindex(")") + 2:].split()
    except (OSError, ValueError):
        return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # it exists, under another user (Windows: access denied is alive too)
    except OSError:
        return False
    f = _stat_fields(pid)
    return not (f and f[0] == "Z")


PROC = "/proc"
COMM_LEN = 15


def _full_name(entry: str, comm: str) -> str:
    if len(comm) < COMM_LEN:
        return comm
    try:
        with open(f"{PROC}/{entry}/cmdline", "rb") as fh:
            argv0 = fh.read().split(b"\0", 1)[0].decode("utf-8", "replace")
    except OSError:
        return comm
    base = os.path.basename(argv0.replace(chr(92), "/"))
    return base if base.startswith(comm) else comm


def process_table() -> list[tuple[int, int, str]]:
    rows = []
    for entry in os.listdir(PROC):
        if not entry.isdigit():
            continue
        try:
            with open(f"{PROC}/{entry}/stat", encoding="utf-8") as fh:
                raw = fh.read()
            name = raw[raw.index("(") + 1:raw.rindex(")")]
            after = raw[raw.rindex(")") + 2:].split()
            if after[0] == "Z":
                continue
            rows.append((int(entry), int(after[1]), _full_name(entry, name)))
        except (OSError, ValueError, IndexError):
            continue
    return rows


def adopted(pid: int, ppid: int) -> bool:
    mine = os.getsid(0)
    own, parent = _stat_fields(pid), _stat_fields(ppid)
    if not own or not parent:
        return False
    return own[3] == str(mine) and parent[3] != str(mine)


def group_members(pgid: int) -> list[int]:
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


def limits_note(timeout_s: int, memory_bytes: int, max_processes: int) -> str:
    return (f"rlimits (kernel-enforced): address space {memory_bytes // 2**20} MB, "
            f"CPU {timeout_s} s, file size 64 MB. Processes: at most "
            f"{max_processes} in this run's process group, enforced by a watchdog "
            f"every {WATCH_INTERVAL_S:g} s - REACTIVE, not kernel-enforced.")


def over_limits(n_processes: int, max_processes: int,
                footprint_bytes: int | None = None,
                memory_bytes: int | None = None) -> str | None:
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
    from . import current

    while proc.poll() is None:
        try:
            members = current.group_members(proc.pid)
            footprint = None
            if memory_bytes is not None:
                footprint = sum(current.process_memory_bytes(p) or 0 for p in members)
            reason = over_limits(len(members), max_processes, footprint, memory_bytes)
        except Exception:  # noqa: BLE001
            reason = None
        if reason:
            proc.limit_reason = reason
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            return
        time.sleep(WATCH_INTERVAL_S)


def spawn_limited(argv: list[str], *, timeout_s: int, memory_bytes: int,
                  max_processes: int, fsize_bytes: int,
                  memory_by_rlimit: bool = True, **popen):
    from ..frozen import interpreter_argv

    limits = {"cpu": int(timeout_s) + 1, "fsize": int(fsize_bytes)}
    if memory_by_rlimit:
        limits["as"] = int(memory_bytes)
    wrapped = [*interpreter_argv(), "-c", LIMITER, json.dumps(limits), *argv]
    proc = subprocess.Popen(wrapped, start_new_session=True, **popen)  # noqa: S603
    proc.limit_reason = None
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
    if getattr(proc, "limit_reason", None):
        return f"\nsandbox: {proc.limit_reason}"
    if proc.returncode == -getattr(signal, "SIGXCPU", 24):
        return f"\nsandbox: CPU time limit reached ({timeout_s} s)"
    if proc.returncode == -getattr(signal, "SIGXFSZ", 25):
        return "\nsandbox: file size limit reached (64 MB)"
    return ""


def background_popen_flags() -> dict:
    return {}


def hold_awake(on: bool) -> None:
    return None


def lock_file(fh) -> None:
    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock_file(fh) -> None:
    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
