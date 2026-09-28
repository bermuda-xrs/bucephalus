"""The platform layer: every OS-specific operation, behind named functions.

A port is filling in `INTERFACE` for a new module, not hunting for
conditionals: `tests/test_platform_layer.py` fails if any module here lacks a
name, and if a `sys.platform` / `os.name` check appears anywhere else in the
package. `current` is chosen once, here.

    windows   has run: every function, on Windows 11, in this project's record.
    posix     Linux. HAS NEVER RUN ANYWHERE. Its five known defects were
              fixed from reading (its header); verification is the
              docs/PLATFORMS.md checklist on a real machine.
    darwin    macOS: posix, plus a process table without /proc and a
              memory cap by watchdog - REACTIVE, because Darwin does not
              enforce RLIMIT_AS. NEVER RUN ANYWHERE either.
"""

from __future__ import annotations

import sys

# name -> what it must do. The contract a port fills in.
INTERFACE = {
    # processes (agent.proc)
    "kill_tree_signal": "signal pid and every descendant to die, now; no waiting",
    "alive": "is pid running",
    "process_table": "[(pid, ppid, image name)] for every visible process",
    "process_start_time": "a creation stamp for pid, comparable only with itself",
    "process_memory_bytes": "resident (or private, by field) bytes of pid, or None",
    "memory_status": "{available_gb, committed_gb, physical_gb, commit_ratio}",
    "free_memory_gb": "physical memory available, GB",
    # the sandbox's kernel-enforced limits (agent.execute.sandbox)
    "limits_note": "what the limits are on this platform, and what is missing",
    "spawn_limited": "start argv with memory/CPU/process limits applied before "
                     "its first instruction; returns (proc, closer); raises "
                     "LimitsRefused if they cannot be applied",
    "exit_note": "text naming the limit that ended this child (CPU, file "
                 "size, a watchdog's verdict), else ''",
    # paths (agent.security.capabilities, the sandbox's fence)
    "has_data_streams": "whether a path component may name an alternate data "
                        "stream (`name:stream`) - NTFS; refused wherever seen",
    # file locking (agent.runlock)
    "lock_file": "exclusive, non-blocking lock on an open file; raises OSError if held",
    "unlock_file": "release lock_file's lock",
}


class LimitsRefused(RuntimeError):
    """The limits could not be applied, so the child must not run."""


if sys.platform == "win32":
    from . import windows as current
elif sys.platform == "darwin":
    from . import darwin as current
else:
    from . import posix as current

__all__ = ["INTERFACE", "LimitsRefused", "current"]
