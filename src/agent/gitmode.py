from __future__ import annotations

import functools
import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path

WORKTREES = ".bucephalus-worktrees"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MIN_GIT = (2, 40)
IDENTITY = {"GIT_AUTHOR_NAME": "bucephalus", "GIT_AUTHOR_EMAIL": "bucephalus@localhost",
            "GIT_COMMITTER_NAME": "bucephalus", "GIT_COMMITTER_EMAIL": "bucephalus@localhost",
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1", "GIT_ATTR_SOURCE": EMPTY_TREE}
HARDEN = ("-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", "-c", f"core.attributesFile={os.devnull}",
          "-c", "core.autocrlf=false", "-c", "core.protectNTFS=true", "-c", "core.protectHFS=true")


class GitModeError(RuntimeError):
    pass


@functools.lru_cache(maxsize=1)
def git_version() -> tuple[int, ...]:
    out = subprocess.run(["git", "version"], capture_output=True, text=True, timeout=30).stdout  # noqa: S603, S607
    m = re.search(r"(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _git(where, *args, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    if git_version() < MIN_GIT:
        raise GitModeError(f"git mode needs git {MIN_GIT[0]}.{MIN_GIT[1]} or later: older git ignores the empty "
                           "attribute source, so a filter in the repository's config could run")
    r = subprocess.run(["git", "-C", str(where), *HARDEN, *args], capture_output=True, text=True,  # noqa: S603, S607
                       encoding="utf-8", errors="replace", timeout=120, env={**os.environ, **IDENTITY, **(env or {})})
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


def git_dirs(where) -> tuple[Path, Path]:
    gitdir = Path(_git(where, "rev-parse", "--absolute-git-dir").stdout.strip()).resolve()
    common = Path(_git(where, "rev-parse", "--git-common-dir").stdout.strip())
    return gitdir, (common if common.is_absolute() else Path(where) / common).resolve()


def _dotgit(top: Path) -> str:
    p = top / ".git"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"


def identity(top: Path) -> dict:
    gitdir, common = git_dirs(top)
    return {"gitdir": str(gitdir), "common": str(common), "dotgit": _dotgit(top)}


def commit_scope(root, scopes: list[str], branch: str, message: str, expect: dict | None = None) -> str | None:
    top = toplevel(root)
    if top is None or not scopes:
        return None
    if expect is not None and identity(top) != expect:
        raise GitModeError("the worktree no longer points at the repository it was made from")
    gitdir, common = git_dirs(top)
    for s in scopes:
        p = (Path(root).resolve() / s).resolve()
        for guarded in (gitdir, common, top / ".git"):
            if guarded == p or p in guarded.parents:
                raise GitModeError(f"the artifact scope {s!r} covers the repository's .git; git mode refuses it")
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


def add_worktree(root, name: str, branch: str) -> tuple[Path, str, dict]:
    top = toplevel(root)
    if top is None:
        raise GitModeError(f"{root} is not inside a git repository")
    path = Path(root).resolve() / WORKTREES / name
    _git(top, "worktree", "add", "-q", "-b", branch, str(path), "HEAD")
    _, common = git_dirs(top)
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    line = f"/{os.path.relpath(Path(root).resolve() / WORKTREES, top).replace(os.sep, '/')}/"
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if line not in text.splitlines():
        exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + line + "\n", encoding="utf-8")
    return path, os.path.relpath(Path(root).resolve(), top), identity(path)
