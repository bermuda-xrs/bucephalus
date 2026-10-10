from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
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


def diagnose(path: str | Path) -> str | None:
    p = Path(path)
    if p.suffix != ".py" or not p.is_file():
        return None
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
    return ("diagnostics:" + NL + NL.join(lines))[:LIMIT]
