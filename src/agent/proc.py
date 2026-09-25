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

The FOURTH instance was here, in the reaper itself, and it points the other
way. `reap_orphans` matched on process NAME alone and killed every match, so
it could not tell a live child of a running Ollama from a true orphan. It
killed the warm model three times during one measurement session, defeating
`keep_alive: 30m`. Because R0 runs first in a ladder, R0's first sample then
absorbed a ~58 s cold load, which inflated the very R0-to-R1 wall-clock drop
the harness-tax finding cites as evidence.

So the class has two directions, not one:

  * anything spawned outlives what spawned it   (the original three), and
  * the reaper cannot tell a live child from an orphan  (this one).

The test is now PARENTAGE, not name: a process is an orphan when its parent
is gone. PID reuse is handled by comparing creation times - a "parent" that
started AFTER its child is a recycled pid, not the real parent. When
parentage genuinely cannot be established the process is left ALIVE, because
the two errors are not symmetric: wrongly killing a healthy worker corrupts
every subsequent measurement in the run, while wrongly sparing an orphan
costs memory that `free_memory_gb()` will report and the preflight will
surface.
"""

from __future__ import annotations

import time

from .platform import current as _platform

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
        _platform.kill_tree_signal(pid, timeout_s)
    except Exception:  # noqa: BLE001 - already gone is success
        pass

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    return not _alive(pid)


def _alive(pid: int) -> bool:
    return _platform.alive(pid)


def process_table() -> list[tuple[int, int, str]]:
    """(pid, ppid, image name) for every visible process. [] if unavailable.

    `tasklist` cannot report a parent pid, which is why the original reaper
    matched on name alone. CreateToolhelp32Snapshot can, needs no extra
    dependency, and costs one syscall rather than one subprocess per lookup.
    """
    try:
        return _platform.process_table()
    except Exception:  # noqa: BLE001 - never let bookkeeping stop a run
        return []


def process_start_time(pid: int) -> int | None:
    """Monotonic-ish creation stamp for `pid`, or None if unreadable.

    Only ever compared against another value from this same function, so the
    unit does not matter - Windows FILETIME ticks and Linux jiffies-since-boot
    are both fine.
    """
    try:
        return _platform.process_start_time(pid)
    except Exception:  # noqa: BLE001
        return None


def _name_matches(image: str, names: tuple[str, ...]) -> bool:
    stem = image.lower()
    if stem.endswith(".exe"):
        stem = stem[:-4]
    return any(stem == n.lower() for n in names)


def orphan_pids(names: tuple[str, ...] = ORPHAN_NAMES,
                protect_pids: tuple[int, ...] = ()) -> list[int]:
    """Pids matching `names` whose parent is GONE. Selection only, no killing.

    Split out from `reap_orphans` so the decision can be tested without
    killing anything - the defect was never in the kill, it was in deciding
    what to kill.

    A process is spared when its parent is still alive, when it is named in
    `protect_pids`, or when parentage cannot be determined at all. Sparing is
    the safe direction: a wrongly-killed worker silently corrupts the run that
    follows it, whereas a wrongly-spared orphan shows up in free memory.
    """
    table = process_table()
    if not table:
        return []
    live = {pid: ppid for pid, ppid, _ in table}
    out = []
    for pid, ppid, image in table:
        if pid in protect_pids or not _name_matches(image, names):
            continue
        if ppid > 0 and ppid in live:
            p_start, c_start = process_start_time(ppid), process_start_time(pid)
            if p_start is None or c_start is None:
                continue                       # cannot disprove -> leave alive
            if p_start <= c_start:
                continue                       # real, living parent -> healthy
            # parent started AFTER the child: a recycled pid, so the true
            # parent is gone and this really is an orphan.
        out.append(pid)
    return out


def reap_orphans(names: tuple[str, ...] = ORPHAN_NAMES,
                 protect_pids: tuple[int, ...] = ()) -> list[int]:
    """Kill orphaned workers - those whose parent is gone. Returns pids reaped.

    Called before a timed run: an orphan's memory is charged to whatever runs
    next, and on a 15.5 GB box that is the difference between 88s and 1500s
    for the same model call.

    It does NOT kill live children of a running server. It used to, which cost
    a cold model load on the first sample of every run - see the module
    docstring.
    """
    reaped = []
    for pid in orphan_pids(names, protect_pids):
        if kill_tree(pid):
            reaped.append(pid)
    return reaped


def worker_rss_gb(names: tuple[str, ...] = ORPHAN_NAMES,
                  field: str = "WorkingSetSize") -> float | None:
    """Resident memory held by the model worker(s), or None if unreadable.

    L2. The leak that aborted Phase 2A was INFERRED from a monotonic commit
    ratio and then isolated by hand. Recording the worker's own RSS per task
    makes it observable directly: if this climbs across a run after the
    refresh in L1 is in place, the refresh is not working and the run says so
    on its face instead of needing a post-mortem.

    Measured anchors on this hardware, granite4.1:3b at 16k context:
        fresh                     3.41 GB
        after 23 benchmark tasks  4.86 GB   (+139 MB commitment per task)
        after unload + reload     3.41 GB   (fully reclaimed)
    """
    try:
        total, found = 0, False
        for pid, _ppid, image in process_table():
            if not _name_matches(image, names):
                continue
            try:
                got = _platform.process_memory_bytes(pid, field)
            except NotImplementedError:
                return None              # D1: this platform has no such metric
            except (OSError, ValueError, IndexError):
                continue
            if got is None:              # could not open this one process
                continue
            total += got
            found = True
        return round(total / 1024 ** 3, 3) if found else None
    except Exception:  # noqa: BLE001
        return None


def worker_private_gb(names: tuple[str, ...] = ORPHAN_NAMES) -> float | None:
    """D1. Private commit, which Windows does NOT trim under pressure.

    Working set read 2.28 -> 3.251 GB across a successful reload because the
    worker had been trimmed first, so a leak signal keyed off working set
    reads the trim rather than the leak. `ollama app.exe` showed 3728 MB
    working set against 60 MB private seconds apart.
    """
    return worker_rss_gb(names, field="PagefileUsage")


def memory_pressure() -> dict:
    """Is the machine THRASHING, as distinct from merely busy?

    Free memory alone cannot answer that, and assuming it could was the sixth
    instrument bug found in this project. A per-task check was added with the
    house rule's 2 GB floor, but that floor was written for PREFLIGHT, with no
    model loaded. Measured here with `granite4.1:3b` legitimately resident at
    16k context, available memory is ~1.2-1.5 GB - so a 2 GB floor flags 100%
    of a perfectly healthy run, and an abort rule built on it would kill that
    run a few tasks in. The check scored "suspect" for a run that behaved
    correctly, which is the question this project asks of every new check.

    Two anchor points, both measured on this hardware (15.47 GB physical):

        healthy, 3B resident, 16k ctx   available 1.17 GB   commit ratio 1.231
        pathological, orphaned worker   available 0.90 GB   commit ratio 1.574

    Available memory barely separates those. The COMMIT RATIO - promised
    memory over physical memory - separates them cleanly, because thrashing
    is precisely the state of having promised far more than exists. The
    pathological row is the documented orphan incident from this module's own
    docstring, where one model call took 1499.8 s for 88.1 s of work.

    This is a TWO-POINT calibration and that is thin. Both anchors are named
    above so a later session can refine rather than re-derive them.
    """
    try:
        return _platform.memory_status()
    except Exception:  # noqa: BLE001 - bookkeeping must never stop a run
        return {}


def free_memory_gb() -> float | None:
    """Physical memory free, or None if it cannot be read."""
    try:
        return _platform.free_memory_gb()
    except Exception:  # noqa: BLE001 - a missing metric must not stop a run
        return None
