"""The platform layer: every OS-specific operation, behind named functions.

A port is filling in `INTERFACE` for a new module, not hunting for
conditionals: `tests/test_platform_layer.py` fails if any module here lacks a
name, and if a `sys.platform` / `os.name` check appears anywhere else in the
package. `current` is chosen once, here.

    windows   has run: every function, on Windows 11, in this project's record.
    posix     HAS NEVER RUN ANYWHERE. It is the pre-refactor code moved
              verbatim, and its known defects are listed in
              docs/PLATFORMS.md - read that before trusting it on a new
              machine. macOS uses it too, and most of it reads /proc, which
              macOS does not have.
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
    "cpu_limit_note": "text for a child the CPU limit killed, else ''",
    # file locking (agent.runlock)
    "lock_file": "exclusive, non-blocking lock on an open file; raises OSError if held",
    "unlock_file": "release lock_file's lock",
}


class LimitsRefused(RuntimeError):
    """The limits could not be applied, so the child must not run."""


if sys.platform == "win32":
    from . import windows as current
else:
    from . import posix as current

__all__ = ["INTERFACE", "LimitsRefused", "current"]
