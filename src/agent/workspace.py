"""Reset a benchmark task's workspace so every arm starts from identical bytes.

`provision()` used to only WRITE the declared fixtures. Anything a previous
run left behind survived — including `output/answer.json`.

That is a measurement-integrity bug, not untidiness. `verify_program` reads
files off disk, so a run that produces no answer is graded against the
PREVIOUS run's answer. Two ways that corrupts a result:

  * a repeat pass of the noise floor scores run 2 using run 1's output, so a
    flaky task looks stable;
  * in a paired trial the two arms share one workspace, so the CONTROL arm
    can be graded against the answer the SKILL arm just wrote. That
    manufactures exactly the lift the gate exists to detect.

So the directory is removed and rebuilt from the declared fixtures before
every arm of every run. The old docstring already claimed "every arm starts
from identical bytes"; this makes it true.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path


class UnsafeScope(RuntimeError):
    """Refused to delete something that is not a task workspace."""


def _resolve(root: Path, rel: str) -> Path:
    return Path(root, rel).resolve()


def reset_workspace(root: str | Path, artifact_scope: list[str],
                    fixtures: dict[str, str] | None) -> list[Path]:
    """Delete each scope directory, then write the fixtures fresh.

    Returns the directories that were cleared, for logging.

    Deliberately conservative about what it will delete: the path must be a
    real subdirectory of `root`, must not be `root` itself, and must not be
    one of the protected directories the capability gate also refuses. A
    benchmark harness that can be pointed at the wrong directory is worse
    than one that leaves stale files.
    """
    root = Path(root).resolve()
    protected = {"anchor", "skills_store", "src", "tests", "bench", "spec",
                 "docs", "examples", ".git", "themes", "assets"}
    cleared: list[Path] = []

    for rel in artifact_scope or []:
        target = _resolve(root, rel)
        if target == root:
            raise UnsafeScope(f"artifact_scope {rel!r} resolves to the project root")
        try:
            inside = target.relative_to(root)
        except ValueError as e:
            raise UnsafeScope(
                f"artifact_scope {rel!r} resolves outside the project root"
            ) from e
        if not inside.parts:
            raise UnsafeScope(f"artifact_scope {rel!r} is empty")
        if inside.parts[0] in protected:
            raise UnsafeScope(
                f"refusing to clear {rel!r}: {inside.parts[0]!r} is protected"
            )
        if target.exists():
            _empty(target)
            cleared.append(target)

    for rel, content in (fixtures or {}).items():
        path = _resolve(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    return cleared


def _empty(target: Path, attempts: int = 5) -> None:
    r"""Delete every FILE under `target`, leaving the directories.

    Not `shutil.rmtree`. Removing directories fails on Windows whenever
    anything holds a handle inside them, and this project lives under
    OneDrive, whose sync client does exactly that:

        PermissionError: [WinError 5] Access is denied: '...\work\M16\input'

    That killed a noise-floor pass mid-run. Deleting files achieves what the
    reset is actually for - no stale `output/answer.json` to be graded
    against - and leaves empty directories, which are harmless and which the
    fixtures repopulate anyway.

    A file that cannot be deleted RAISES rather than being skipped. Leaving a
    stale answer behind is the exact corruption this module exists to
    prevent, so failing loudly beats proceeding with a workspace that might
    fabricate a pass.
    """
    for path in sorted(target.rglob("*"), key=lambda q: -len(q.parts)):
        if path.is_dir():
            continue
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                path.unlink()
                break
            except PermissionError as e:      # read-only, or a sync handle
                last = e
                try:
                    os.chmod(path, stat.S_IWRITE)
                except OSError:
                    pass
                time.sleep(0.1 * (attempt + 1))
            except FileNotFoundError:
                break
        else:
            raise UnsafeScope(
                f"could not delete {path} after {attempts} attempts: {last}. "
                "A stale file here can be graded as this run's answer, so the "
                "run is stopped rather than risking a fabricated pass. "
                "Something holds a handle on it - on Windows this is usually "
                "OneDrive or an indexer; excluding the work/ directory from "
                "sync fixes it permanently."
            )
