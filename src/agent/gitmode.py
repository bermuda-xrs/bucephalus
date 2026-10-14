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


def _git(where, *args, env: dict | None = None, check: bool = True, data: bytes | None = None,
         text: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(where), *HARDEN, *args], input=data, capture_output=True,  # noqa: S603, S607
                       timeout=120, env={**os.environ, **IDENTITY, **(env or {})})
    if check and r.returncode != 0:
        raise GitModeError(f"git {args[0]} failed: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    if text:
        r.stdout = r.stdout.decode("utf-8", "replace")
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
        _stage(top, rels, env)
        tree = _git(top, "write-tree", env=env).stdout.strip()
        sha = _git(top, "commit-tree", tree, "-m", message, *(["-p", head] if head else []), env=env).stdout.strip()
    _git(top, "update-ref", f"refs/heads/{branch}", sha)
    return sha


def _inside(top: Path, p: Path) -> bool:
    try:
        return not p.is_symlink() and p.resolve().is_relative_to(top) and p.is_file()
    except OSError:
        return False


def _stage(top: Path, rels: list[str], env: dict) -> None:
    cached = [f for f in _git(top, "ls-files", "-z", "--cached", "--", *rels, env=env).stdout.split(chr(0)) if f]
    others = [f for f in _git(top, "ls-files", "-z", "--others", "--exclude-standard", "--", *rels,
                              env=env).stdout.split(chr(0)) if f]
    files = [f for f in dict.fromkeys(cached + others) if chr(10) not in f and _inside(top, top / f)]
    info = "".join(f"0 {'0' * 40}\t{f}" + chr(0) for f in cached if f not in files)
    for f in files:
        p = top / f
        if not _inside(top, p):
            continue
        sha = _git(top, "hash-object", "--no-filters", "-w", "--stdin", data=p.read_bytes()).stdout.strip()
        info += f"100644 {sha}\t{f}" + chr(0)
    if info:
        _git(top, "update-index", "-z", "--add", "--remove", "--index-info", env=env, data=info.encode("utf-8"))


RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
IGNORABLE = dict.fromkeys([*range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x2065), 0xFEFF])


def safe_name(name: str) -> bool:
    for part in name.split("/"):
        bare = part.translate(IGNORABLE)
        low = bare.lower()
        if (not part or bare != part or part in (".", "..") or ":" in part or chr(92) in part
                or any(ord(c) < 32 or ord(c) == 127 for c in part)
                or part != part.rstrip(". ") or low == ".git" or low.startswith("git~")
                or low.split(".", 1)[0] in RESERVED):
            return False
    return True


def _populate(top: Path, path: Path, branch: str) -> None:
    before = git_dirs(path)[1]
    listing = _git(top, "ls-tree", "-r", "-z", branch).stdout
    entries = []
    for item in listing.split(chr(0)):
        if not item:
            continue
        meta, _, name = item.partition("\t")
        mode, kind, sha = meta.split()
        if kind == "blob" and mode in ("100644", "100755") and safe_name(name):
            entries.append((sha, name))
    for sha, name in entries:
        blob = _git(top, "cat-file", "blob", sha, text=False).stdout
        target = (path / name)
        if not target.resolve().is_relative_to(path.resolve()):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    if git_dirs(path)[1] != before:
        raise GitModeError("filling the worktree changed where its git dir points")
    _git(path, "read-tree", branch)


def add_worktree(root, name: str, branch: str) -> tuple[Path, str, str]:
    top = toplevel(root)
    if top is None:
        raise GitModeError(f"{root} is not inside a git repository")
    path = Path(root).resolve() / WORKTREES / name
    _git(top, "worktree", "add", "-q", "--no-checkout", "-b", branch, str(path), "HEAD")
    _populate(top, path, branch)
    _, common = git_dirs(top)
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    line = f"/{os.path.relpath(Path(root).resolve() / WORKTREES, top).replace(os.sep, '/')}/"
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if line not in text.splitlines():
        exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + line + "\n", encoding="utf-8")
    return path, os.path.relpath(Path(root).resolve(), top), str(common)
