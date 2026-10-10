from __future__ import annotations

import shlex

TESTLIKE_MODULES = {"pytest", "unittest", "py_compile", "ruff", "mypy", "flake8", "compileall"}
PYTHONS = {"python", "python3", "py"}
PYTEST_QUIET = ["-p", "no:cacheprovider", "-p", "no:logging", "-p", "no:faulthandler"]


class CommandRefused(ValueError):
    pass


def _program(module: str | None, file: str | None, argv: list[str]) -> str:
    lines = ["import os, runpy, sys", "sys.dont_write_bytecode = True", "sys.path.insert(0, os.getcwd())",
             f"sys.argv = {argv!r}"]
    if module:
        lines.append(f"runpy.run_module({module!r}, run_name='__main__', alter_sys=True)")
    else:
        lines.append(f"runpy.run_path({file!r}, run_name='__main__')")
    return chr(10).join(lines) + chr(10)


def to_program(command: str) -> tuple[str, bool]:
    try:
        words = shlex.split(command)
    except ValueError as e:
        raise CommandRefused(f"cannot parse the command: {e}") from None
    if not words:
        raise CommandRefused("an empty command")
    head, rest = words[0], words[1:]
    if head == "pytest":
        return _program(head, None, [head, *PYTEST_QUIET, *rest]), True
    if head == "ruff":
        return _program(head, None, [head, *rest]), True
    if head in PYTHONS:
        if rest[:1] == ["-m"] and len(rest) >= 2:
            quiet = PYTEST_QUIET if rest[1] == "pytest" else []
            return _program(rest[1], None, [rest[1], *quiet, *rest[2:]]), rest[1] in TESTLIKE_MODULES
        if rest and not rest[0].startswith("-"):
            return _program(None, rest[0], rest), True
    raise CommandRefused(
        "run_command runs `python FILE [args]`, `python -m MODULE [args]`, `pytest` or `ruff` only "
        "(no -c, no shell). To read or search files use read_file and search.")
