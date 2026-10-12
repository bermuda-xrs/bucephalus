from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .platform import current as _platform


class RunLockBusy(RuntimeError):
    ...


STATUS_SUFFIX = ".holder.json"


def status_path(lock_path) -> Path:
    p = Path(lock_path)
    return p.with_name(p.name + STATUS_SUFFIX)


def measurement_lock_path(root) -> Path:
    return Path(root) / ".measurement.lock"


def task_lock(ctx, task_id: str) -> "RunLock":
    return RunLock(ctx.config.resolve_state("tasklocks") / f"{task_id}.lock", label="task run")


def lock_status(lock_path) -> dict:
    try:
        info = json.loads(status_path(lock_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"held": False}
    from .proc import _alive

    pid = int(info.get("pid") or 0)
    if pid and _alive(pid):
        return {"held": True, **info}
    return {"held": False, "stale": True, **info}


_HELD: set = set()


class RunLock:
    def __init__(self, path: str | Path, label: str = "measurement"):
        self.path = Path(path)
        self.label = label
        self._fh = None

    def acquire(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._fh = open(self.path, "r+", encoding="utf-8")
        try:
            self._lock()
        except OSError:
            holder = "unknown"
            try:
                self._fh.seek(0)
                holder = self._fh.read().strip() or "unknown"
            except OSError:
                pass
            self._fh.close()
            self._fh = None
            why = ("Two measurement processes on one database corrupt the "
                   "result: tasks get measured under CPU contention, which can "
                   "manufacture `budget_exhausted` on a task that would "
                   "otherwise finish.\n") if self.label == "measurement" else ""
            raise RunLockBusy(
                f"another {self.label} process holds {self.path.name} "
                f"(started by PID {holder}).\n" + why +
                "Wait for it, or kill it and retry — the lock releases "
                "automatically when the process dies."
            ) from None

        self._fh.seek(0)
        self._fh.write(f"{os.getpid():<20}")
        self._fh.flush()
        _HELD.add(self)
        sp = status_path(self.path)
        tmp = sp.with_name(sp.name + ".tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), "label": self.label,
                                   "started_at": time.time()}), encoding="utf-8")
        os.replace(tmp, sp)
        return self

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            sp = status_path(self.path)
            if json.loads(sp.read_text(encoding="utf-8")).get("pid") == os.getpid():
                sp.unlink()
        except (OSError, ValueError):
            pass
        try:
            self._unlock()
        except OSError:
            pass
        finally:
            self._fh.close()
            self._fh = None
            _HELD.discard(self)

    def _lock(self) -> None:
        _platform.lock_file(self._fh)

    def _unlock(self) -> None:
        _platform.unlock_file(self._fh)

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
