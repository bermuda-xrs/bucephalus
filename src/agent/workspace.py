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

import shutil
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
            shutil.rmtree(target)
            cleared.append(target)

    for rel, content in (fixtures or {}).items():
        path = _resolve(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    return cleared
