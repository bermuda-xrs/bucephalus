from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import agent


def src_digest(pkg: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    for p in sorted(Path(pkg).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        h.update(p.relative_to(pkg).as_posix().encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
        n += 1
    return h.hexdigest(), n


def _git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20)  # noqa: S603,S607
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def code_identity() -> dict:
    pkg = Path(agent.__file__).resolve().parent
    digest, n = src_digest(pkg)
    status = _git(pkg, "status", "--porcelain", "--", str(pkg))
    return {"harness_version": agent.HARNESS_VERSION, "toolset_version": agent.TOOLSET_VERSION,
            "src_sha256": digest, "src_files": n, "git_commit": _git(pkg, "rev-parse", "HEAD"),
            "git_dirty": None if status is None else bool(status)}
