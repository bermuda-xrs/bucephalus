from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

WORKTREES = ".bucephalus-worktrees"
IDENTITY = {"GIT_AUTHOR_NAME": "bucephalus", "GIT_AUTHOR_EMAIL": "bucephalus@localhost",
            "GIT_COMMITTER_NAME": "bucephalus", "GIT_COMMITTER_EMAIL": "bucephalus@localhost",
            "GIT_TERMINAL_PROMPT": "0"}


class GitModeError(RuntimeError):
    pass


def _git(where, *args, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(where), "-c", f"core.hooksPath={os.devnull}", *args],  # noqa: S603, S607
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                       env={**os.environ, **IDENTITY, **(env or {})})
    if check and r.returncode != 0:
        raise GitModeError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def toplevel(root) -> Path | None:
    try:
        r = _git(root, "rev-parse", "--show-toplevel", check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = r.stdout.strip()
    return Path(out).resolve() if r.returncode == 0 and out else None


def commit_scope(root, scopes: list[str], branch: str, message: str) -> str | None:
    top = toplevel(root)
    if top is None or not scopes:
        return None
    with tempfile.TemporaryDirectory(prefix="bucephalus-gitmode-") as d:
        env = {"GIT_INDEX_FILE": str(Path(d) / "index")}
        head = _git(top, "rev-parse", "--verify", "-q", "HEAD", check=False).stdout.strip()
        if head:
            _git(top, "read-tree", head, env=env)
        rels = [os.path.relpath(Path(root).resolve() / s, top) for s in scopes]
        _git(top, "add", "-A", "--", *rels, env=env)
        tree = _git(top, "write-tree", env=env).stdout.strip()
        sha = _git(top, "commit-tree", tree, "-m", message, *(["-p", head] if head else []), env=env).stdout.strip()
    _git(top, "update-ref", f"refs/heads/{branch}", sha)
    return sha


def add_worktree(root, name: str, branch: str) -> tuple[Path, str]:
    top = toplevel(root)
    if top is None:
        raise GitModeError(f"{root} is not inside a git repository")
    path = Path(root).resolve() / WORKTREES / name
    _git(top, "worktree", "add", "-q", "-b", branch, str(path), "HEAD")
    common = Path(_git(top, "rev-parse", "--git-common-dir").stdout.strip())
    exclude = (common if common.is_absolute() else top / common) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    line = f"/{os.path.relpath(Path(root).resolve() / WORKTREES, top).replace(os.sep, '/')}/"
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if line not in text.splitlines():
        exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + line + "\n", encoding="utf-8")
    return path, os.path.relpath(Path(root).resolve(), top)
