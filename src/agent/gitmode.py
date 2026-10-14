from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

WORKTREES = ".bucephalus-worktrees"
IDENTITY = {"GIT_AUTHOR_NAME": "bucephalus", "GIT_AUTHOR_EMAIL": "bucephalus@localhost",
            "GIT_COMMITTER_NAME": "bucephalus", "GIT_COMMITTER_EMAIL": "bucephalus@localhost",
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
HARDEN = ("-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", "-c", f"core.attributesFile={os.devnull}")


class GitModeError(RuntimeError):
    pass


def _filters_off(where) -> list[str]:
    r = subprocess.run(["git", "-C", str(where), *HARDEN, "config", "--local", "--name-only", "--get-regexp",  # noqa: S603, S607
                        r"^filter\."], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                       env={**os.environ, **IDENTITY})
    names = {line.split(".")[1] for line in r.stdout.splitlines() if line.count(".") >= 2}
    out: list[str] = []
    for n in sorted(names):
        out += ["-c", f"filter.{n}.clean=", "-c", f"filter.{n}.smudge=", "-c", f"filter.{n}.process=",
                "-c", f"filter.{n}.required=false"]
    return out


def _git(where, *args, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(where), *HARDEN, *_filters_off(where), *args],  # noqa: S603, S607
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


def git_dirs(where) -> tuple[Path, Path]:
    gitdir = Path(_git(where, "rev-parse", "--absolute-git-dir").stdout.strip()).resolve()
    common = Path(_git(where, "rev-parse", "--git-common-dir").stdout.strip())
    return gitdir, (common if common.is_absolute() else Path(where) / common).resolve()


def commit_scope(root, scopes: list[str], branch: str, message: str, expect_common: str | None = None) -> str | None:
    top = toplevel(root)
    if top is None or not scopes:
        return None
    gitdir, common = git_dirs(top)
    if expect_common is not None and common != Path(expect_common).resolve():
        raise GitModeError("the worktree no longer points at the repository it was made from")
    dotgit = top / ".git"
    for s in scopes:
        p = (Path(root).resolve() / s).resolve()
        for guarded in (gitdir, common, dotgit):
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


def add_worktree(root, name: str, branch: str) -> tuple[Path, str, str]:
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
    return path, os.path.relpath(Path(root).resolve(), top), str(common)
