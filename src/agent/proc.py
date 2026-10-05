from __future__ import annotations

import time

from .platform import current as _platform

ORPHAN_NAMES = ("llama-server",)


def descendants(pid: int) -> list[int]:
    kids: dict[int, list[int]] = {}
    for p, pp, _ in process_table():
        if p != pp:
            kids.setdefault(pp, []).append(p)
    out, frontier = [], [pid]
    while frontier:
        parent = frontier.pop(0)
        p_start = process_start_time(parent)
        for child in kids.get(parent, []):
            c_start = process_start_time(child)
            if p_start is None or c_start is None or c_start < p_start:
                continue
            out.append(child)
            frontier.append(child)
    return out


def kill_tree(pid: int, timeout_s: float = 10.0,
              expect_start: int | None = None) -> bool:
    if pid <= 0:
        return True
    now = process_start_time(pid)
    if expect_start is not None and now != expect_start:
        return True
    try:
        _platform.kill_tree_signal(pid, timeout_s, now)
    except Exception:  # noqa: BLE001
        pass

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _alive(pid: int) -> bool:
    return _platform.alive(pid)


def process_table() -> list[tuple[int, int, str]]:
    try:
        return _platform.process_table()
    except Exception:  # noqa: BLE001
        return []


def process_start_time(pid: int) -> int | None:
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
    table = process_table()
    if not table:
        return []
    live = {pid: ppid for pid, ppid, _ in table}
    out = []
    for pid, ppid, image in table:
        if pid in protect_pids or not _name_matches(image, names):
            continue
        if ppid > 0 and ppid in live and not _platform.adopted(pid, ppid):
            p_start, c_start = process_start_time(ppid), process_start_time(pid)
            if p_start is None or c_start is None:
                continue
            if p_start <= c_start:
                continue
        out.append(pid)
    return out


def reap_orphans(names: tuple[str, ...] = ORPHAN_NAMES,
                 protect_pids: tuple[int, ...] = ()) -> list[int]:
    reaped = []
    for pid in orphan_pids(names, protect_pids):
        start = process_start_time(pid)
        if start is not None and kill_tree(pid, expect_start=start):
            reaped.append(pid)
    return reaped


def worker_rss_gb(names: tuple[str, ...] = ORPHAN_NAMES,
                  field: str = "WorkingSetSize") -> float | None:
    try:
        total, found = 0, False
        for pid, _ppid, image in process_table():
            if not _name_matches(image, names):
                continue
            try:
                got = _platform.process_memory_bytes(pid, field)
            except NotImplementedError:
                return None
            except (OSError, ValueError, IndexError):
                continue
            if got is None:
                continue
            total += got
            found = True
        return round(total / 1024 ** 3, 3) if found else None
    except Exception:  # noqa: BLE001
        return None


def worker_private_gb(names: tuple[str, ...] = ORPHAN_NAMES) -> float | None:
    return worker_rss_gb(names, field="PagefileUsage")


def memory_pressure() -> dict:
    try:
        return _platform.memory_status()
    except Exception:  # noqa: BLE001
        return {}


def free_memory_gb() -> float | None:
    try:
        return _platform.free_memory_gb()
    except Exception:  # noqa: BLE001
        return None
