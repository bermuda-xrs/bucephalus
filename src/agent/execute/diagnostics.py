from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

NL = chr(10)
LIMIT = 1500


def ruff_command() -> list[str] | None:
    found = shutil.which("ruff")
    if found:
        return [found]
    if not getattr(sys, "frozen", False) and importlib.util.find_spec("ruff"):
        return [sys.executable, "-m", "ruff"]
    return None


def _python(p: Path) -> list[str]:
    lines = []
    try:
        compile(p.read_text(encoding="utf-8"), str(p), "exec")
        lines.append("py_compile: ok")
    except SyntaxError as e:
        lines.append(f"py_compile: SyntaxError: {e.msg} (line {e.lineno})")
        if e.text:
            lines.append("  " + e.text.strip())
    except (UnicodeDecodeError, ValueError) as e:
        lines.append(f"py_compile: {type(e).__name__}: {e}")
    cmd = ruff_command()
    if cmd is None:
        lines.append("ruff: not available, so the file was not linted")
    else:
        try:
            r = subprocess.run(  # noqa: S603
                [*cmd, "check", "--no-cache", "--output-format", "concise", str(p)],
                capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
            out = (r.stdout + r.stderr).strip()
            lines.append("ruff: clean" if r.returncode == 0 else "ruff:" + NL + out)
        except (OSError, subprocess.TimeoutExpired) as e:
            lines.append(f"ruff: {type(e).__name__}")
    return lines


def _json(p: Path) -> list[str]:
    try:
        json.loads(p.read_text(encoding="utf-8"))
        return ["json: ok"]
    except json.JSONDecodeError as e:
        return [f"json: JSONDecodeError: {e.msg} (line {e.lineno}, column {e.colno})"]
    except UnicodeDecodeError as e:
        return [f"json: {type(e).__name__}: {e}"]


def _yaml(p: Path) -> list[str]:
    import yaml

    try:
        list(yaml.safe_load_all(p.read_text(encoding="utf-8")))
        return ["yaml: ok"]
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        return [f"yaml: {type(e).__name__}: {getattr(e, 'problem', None) or e}{where}"]
    except UnicodeDecodeError as e:
        return [f"yaml: {type(e).__name__}: {e}"]


def _toml(p: Path) -> list[str]:
    try:
        tomllib.loads(p.read_text(encoding="utf-8"))
        return ["toml: ok"]
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return [f"toml: {type(e).__name__}: {e}"]


def _node(p: Path) -> list[str]:
    node = shutil.which("node")
    if node is None:
        return ["node: not available, so the file was not checked"]
    try:
        r = subprocess.run(  # noqa: S603
            [node, "--check", str(p)], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"node: {type(e).__name__}"]
    return ["node: ok"] if r.returncode == 0 else ["node: " + (r.stderr or r.stdout).strip()]


CHECKS = {".py": _python, ".json": _json, ".yaml": _yaml, ".yml": _yaml, ".toml": _toml,
          ".js": _node, ".mjs": _node, ".cjs": _node}


def diagnose(path: str | Path) -> str | None:
    p = Path(path)
    check = CHECKS.get(p.suffix.lower())
    if check is None or not p.is_file():
        return None
    return ("diagnostics:" + NL + NL.join(check(p)))[:LIMIT]
