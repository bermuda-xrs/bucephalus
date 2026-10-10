from __future__ import annotations

import os
import sys

SANDBOX_CHILD_FLAG = "--bucephalus-sandbox-child"
CHILD_ENV = "BUCEPHALUS_SANDBOX_CHILD"

_ACCEPTED = {"-I", "-u", "-E", "-s", "-S", "-B", "-P"}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def interpreter_argv() -> list[str]:
    if is_frozen():
        return [sys.executable, SANDBOX_CHILD_FLAG]
    return [sys.executable, "-I", "-u"]


def child_entry() -> None:
    if not is_frozen():
        return
    import multiprocessing

    multiprocessing.freeze_support()
    args = sys.argv[1:]
    if args[:1] == [SANDBOX_CHILD_FLAG]:
        _interpret(args[1:])
    if os.environ.get(CHILD_ENV):
        _interpret(args)


def _interpret(args: list[str]) -> None:
    import runpy
    import traceback

    os.environ[CHILD_ENV] = "1"
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
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)
