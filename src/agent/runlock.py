"""One measurement process at a time, enforced by the OS.

Two runs of `noise.py` overlapped on 2026-08-27 because a process that looked
dead was not, and the damage was not the duplicated ledger rows — those
collapse on resume. It was this:

  * three tasks were measured while two models competed for the same CPU, so
    their wall clock is not comparable to anything else in the run, and
  * `budget_exhausted` is a TIME limit, so contention can *manufacture* that
    outcome on a task that would otherwise have finished. The suite then
    looks harder than it is, for reasons that have nothing to do with the
    tasks.

It also breaks a documented invariant: the hash-chain lock is process-wide,
so a second process cannot see it. Chains are per-task, which is why nothing
forked that time — luck, not design.

So: an OS-level advisory lock, which the kernel releases when the process
dies. That property matters here specifically, because these processes are
routinely killed rather than exiting cleanly, and a lock that needed tidy
shutdown would strand every subsequent run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":  # pragma: no cover - platform split
    import msvcrt
else:  # pragma: no cover
    import fcntl


class RunLockBusy(RuntimeError):
    """Another measurement process holds the lock."""


# Held locks, kept alive deliberately. `RunLock(p).acquire()` without binding
# the result would otherwise be collected immediately, closing the file and
# releasing the lock with no error anywhere - a silent unlock, which is the
# worst possible failure for a guard whose whole job is to refuse.
_HELD: set = set()


class RunLock:
    """Advisory whole-file lock, released by the OS on process death.

    Usage:

        with RunLock(path, "noise floor"):
            ...
    """

    def __init__(self, path: str | Path, label: str = "measurement"):
        self.path = Path(path)
        self.label = label
        self._fh = None

    def acquire(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # "r+", not "a+": append mode ignores seek() on write, so every
        # acquire tacked another PID onto the end and the error named a stale
        # one. Touch-then-open keeps the previous holder's PID readable while
        # letting us overwrite it in place once the lock is ours.
        self.path.touch(exist_ok=True)
        self._fh = open(self.path, "r+", encoding="utf-8")
        try:
            self._lock()
        except OSError:
            # Reading the holder's PID touches the very byte they hold, so on
            # Windows the read raises too. Naming them is a nicety; refusing
            # to start is the point, so a failed read must not mask it.
            holder = "unknown"
            try:
                self._fh.seek(0)
                holder = self._fh.read().strip() or "unknown"
            except OSError:
                pass
            self._fh.close()
            self._fh = None
            raise RunLockBusy(
                f"another {self.label} process holds {self.path.name} "
                f"(started by PID {holder}).\n"
                "Two measurement processes on one database corrupt the "
                "result: tasks get measured under CPU contention, which can "
                "manufacture `budget_exhausted` on a task that would "
                "otherwise finish.\n"
                "Wait for it, or kill it and retry — the lock releases "
                "automatically when the process dies."
            ) from None

        # Fixed width, no truncate: truncating a file whose byte 0 is locked
        # is not portable, and the PID only has to be readable, not tidy.
        self._fh.seek(0)
        self._fh.write(f"{os.getpid():<20}")
        self._fh.flush()
        _HELD.add(self)
        return self

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self._unlock()
        except OSError:
            pass
        finally:
            self._fh.close()
            self._fh = None
            _HELD.discard(self)

    # -- platform split ----------------------------------------------------

    def _lock(self) -> None:
        if sys.platform == "win32":
            # msvcrt locks a byte RANGE from the current position, and "a+"
            # leaves that position at EOF. Without this seek every opener
            # locked a different byte and the lock silently did nothing.
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self) -> None:
        if sys.platform == "win32":
            self._fh.seek(0)          # same byte we locked
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
