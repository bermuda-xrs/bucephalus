"""D13 - how a FROZEN build starts a Python for the sandbox without starting
itself.

Unfrozen, the sandbox runs `[sys.executable, "-I", "-u", script]`, and
sys.executable is an interpreter. Frozen (PyInstaller), sys.executable is the
APPLICATION: those arguments reached it as plain argv, every run_sandboxed()
re-launched the whole app, and a minimal probe left eleven nested sandbox
directories and four chained processes (SPEC-DELTA C26).

`multiprocessing.freeze_support()` solves the same problem for multiprocessing
by dispatching on a flag before any application code runs. This is that
mechanism applied to the sandbox's own spawn path, rather than moving the
sandbox onto multiprocessing: a `multiprocessing.Process` child could not be
created SUSPENDED and assigned to its Job Object before its first instruction,
would not get the sandbox's clean environment, and would lose the stdin/stdout
tool bridge. Those are sandbox properties, and a freeze fix may not cost any.

Two rules, both enforced here and nowhere else:

  1. Launched with SANDBOX_CHILD_FLAG, the executable is an INTERPRETER: it
     runs the given script or `-c` code and exits. No application code runs.
  2. Anything descended from a sandbox child is also only an interpreter. The
     child sets CHILD_ENV in its own environment, so if model code spawns
     `sys.executable` - the app's own binary - it gets a Python, exactly as it
     would unfrozen, and never the application. Nothing started from inside
     the sandbox can start the app.

`child_entry()` must be the FIRST thing a frozen entry point calls. This
module imports nothing from the agent, so calling it runs no app code.
"""

from __future__ import annotations

import os
import sys

SANDBOX_CHILD_FLAG = "--alpha-sandbox-child"
CHILD_ENV = "ALPHA_SANDBOX_CHILD"

# The interpreter flags the sandbox passes, accepted and emulated. -I: a
# frozen interpreter's sys.path is the bundle and never the script's
# directory or a user site; -u: stdout/stderr are made write-through below.
_ACCEPTED = {"-I", "-u", "-E", "-s", "-S", "-B", "-P"}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def interpreter_argv() -> list[str]:
    """The argv prefix that starts a Python for sandboxed code."""
    if is_frozen():
        return [sys.executable, SANDBOX_CHILD_FLAG]
    return [sys.executable, "-I", "-u"]


def child_entry() -> None:
    """Dispatch before any application code. Returns only when this process
    is the application."""
    if not is_frozen():
        return
    import multiprocessing

    multiprocessing.freeze_support()      # any library that uses it
    args = sys.argv[1:]
    if args[:1] == [SANDBOX_CHILD_FLAG]:
        _interpret(args[1:])
    if os.environ.get(CHILD_ENV):
        # Rule 2: a descendant of a sandbox child. Never the application.
        _interpret(args)


def _interpret(args: list[str]) -> None:
    """Behave as `python [flags] (-c code | script) [args]`. Never returns."""
    import runpy
    import traceback

    os.environ[CHILD_ENV] = "1"           # rule 2, for everything this starts
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(write_through=True)
        except (AttributeError, ValueError):
            pass
    i = 0
    while i < len(args) and args[i] in _ACCEPTED:
        i += 1
    rest = args[i:]
    try:
        if rest[:1] == ["-c"] and len(rest) > 1:
            sys.argv = ["-c", *rest[2:]]
            exec(compile(rest[1], "<string>", "exec"), {"__name__": "__main__"})  # noqa: S102
        elif rest and not rest[0].startswith("-"):
            sys.argv = list(rest)
            runpy.run_path(rest[0], run_name="__main__")
        else:
            sys.stderr.write(f"{SANDBOX_CHILD_FLAG}: expected -c CODE or a "
                             f"script, got {rest!r}\n")
            sys.exit(2)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - what the interpreter itself does
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)
