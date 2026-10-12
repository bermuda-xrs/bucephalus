from __future__ import annotations

import sys

INTERFACE = {
    "kill_tree_signal": "signal pid and every descendant to die, now; no waiting",
    "alive": "is pid running",
    "process_table": "[(pid, ppid, image name)] for every visible process",
    "process_start_time": "a creation stamp for pid, comparable only with itself",
    "adopted": "whether pid, whose parent is ppid, is an orphan the OS re-parented "
               "(POSIX: to init or a subreaper) rather than a child of a live parent",
    "process_memory_bytes": "resident (or private, by field) bytes of pid, or None",
    "memory_status": "{available_gb, committed_gb, physical_gb, commit_ratio}",
    "free_memory_gb": "physical memory available, GB",
    "limits_note": "what the limits are on this platform, and what is missing",
    "spawn_limited": "start argv with memory/CPU/process limits applied before "
                     "its first instruction; returns (proc, closer); raises "
                     "LimitsRefused if they cannot be applied",
    "exit_note": "text naming the limit that ended this child (CPU, file "
                 "size, a watchdog's verdict), else ''",
    "spawn_denied_processes": "the process limit for code that may not spawn: "
                              "exactly the interpreter's own processes, or None "
                              "where the limit cannot be exact",
    "has_data_streams": "whether a path component may name an alternate data "
                        "stream (`name:stream`) - NTFS; refused wherever seen",
    "link_names": "every name (hard link) of an existing file, as absolute "
                  "paths, or None where the platform cannot list them",
    "lock_file": "exclusive, non-blocking lock on an open file; raises OSError if held",
    "unlock_file": "release lock_file's lock",
    "background_popen_flags": "Popen keyword arguments for a helper server that runs with no window",
    "hold_awake": "keep the machine from sleeping while on is True, from the calling thread; no-op where not needed",
}


class LimitsRefused(RuntimeError):
    ...


if sys.platform == "win32":
    from . import windows as current
elif sys.platform == "darwin":
    from . import darwin as current
else:
    from . import posix as current

__all__ = ["INTERFACE", "LimitsRefused", "current"]
