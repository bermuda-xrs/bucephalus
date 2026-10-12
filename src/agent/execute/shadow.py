from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

BIG_FILE_BYTES = 10 * 1024 * 1024
GIT_TIMEOUT_S = 120


class ShadowUnavailable(RuntimeError):
    pass


def git_available() -> bool:
    return shutil.which("git") is not None


class ShadowRepo:
    def __init__(self, git_dir: Path, work_tree: Path, scopes: list[str]):
        self.git_dir = Path(git_dir)
        self.work_tree = Path(work_tree).resolve()
        self.scopes = scopes

    @classmethod
    def for_task(cls, ctx, task) -> ShadowRepo:
        from ..security.capabilities import is_protected, task_state_paths

        root = Path(ctx.config.root).resolve()
        state = task_state_paths(ctx, task)
        scopes = []
        for s in task.contract.artifact_scope:
            p = ctx.config.resolve(s)
            try:
                rel = p.resolve().relative_to(root)
            except ValueError:
                continue
            if rel.as_posix() == "." or is_protected(str(p), str(root), state):
                continue
            scopes.append(rel.as_posix())
        return cls(ctx.config.resolve_state("shadow") / task.id, root, scopes)

    def _git(self, *args: str, stdin: str | None = None, ok_codes: tuple = (0,)) -> subprocess.CompletedProcess:
        if not git_available():
            raise ShadowUnavailable("git is not installed")
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_TERMINAL_PROMPT": "0"}
        cmd = ["git", "--git-dir", str(self.git_dir), "--work-tree", str(self.work_tree),
               "-c", f"core.hooksPath={os.devnull}", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
               "-c", "core.quotepath=off", "-c", "user.name=bucephalus", "-c", "user.email=bucephalus@localhost",
               "-c", "commit.gpgsign=false", *args]
        r = subprocess.run(cmd, cwd=self.work_tree, env=env, capture_output=True, text=True,  # noqa: S603
                           encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_S, input=stdin)
        if r.returncode not in ok_codes:
            raise ShadowUnavailable(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
        return r

    def ensure(self) -> None:
        if not (self.git_dir / "HEAD").exists():
            self.git_dir.mkdir(parents=True, exist_ok=True)
            self._git("init", "--quiet")

    def _big_files(self) -> list[str]:
        out = []
        for s in self.scopes:
            for p in (self.work_tree / s).rglob("*"):
                try:
                    if p.is_file() and p.stat().st_size > BIG_FILE_BYTES:
                        out.append(p.relative_to(self.work_tree).as_posix())
                except OSError:
                    continue
        return out

    def checkpoint(self, label: str) -> str | None:
        if not self.scopes:
            return None
        self.ensure()
        self._git("add", "-A", "-f", "--ignore-errors", "--", *self.scopes, ok_codes=(0, 1))
        big = self._big_files()
        if big:
            self._git("rm", "--cached", "-q", "--ignore-unmatch", "--", *big)
        self._git("commit", "--allow-empty", "--no-verify", "-q", "-m", label)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def restore(self, sha: str) -> None:
        self.ensure()
        dirs = [self.work_tree / s for s in self.scopes if (self.work_tree / s).is_dir()]
        self._git("read-tree", "--reset", "-u", sha)
        self._git("clean", "-fdxq", "--", *self.scopes)
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    def diff(self, a: str, b: str) -> str:
        return self._git("diff", "--no-color", "--no-ext-diff", a, b, "--", *self.scopes).stdout

    def changed(self, a: str, b: str) -> list[str]:
        return self._git("diff", "--name-only", a, b, "--", *self.scopes).stdout.split()

    def log(self) -> list[tuple[str, str]]:
        if not (self.git_dir / "HEAD").exists():
            return []
        r = self._git("log", "--format=%H %s")
        return [tuple(line.split(" ", 1)) for line in r.stdout.splitlines() if line]
