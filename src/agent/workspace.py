from __future__ import annotations

import os
import stat
import time
from pathlib import Path


class UnsafeScope(RuntimeError):
    ...


def _resolve(root: Path, rel: str) -> Path:
    return Path(root, rel).resolve()


PROTECTED = frozenset({"anchor", "skills_store", "src", "tests", "bench", "spec", "docs", "examples", ".git", "themes",
                       "assets"})


def _inside(root: Path, rel: str, what: str) -> Path:
    target = _resolve(root, rel)
    if target == root:
        raise UnsafeScope(f"{what} {rel!r} resolves to the project root")
    try:
        inside = target.relative_to(root)
    except ValueError as e:
        raise UnsafeScope(f"{what} {rel!r} resolves outside the project root") from e
    if inside.parts[0] in PROTECTED:
        raise UnsafeScope(f"refusing {what} {rel!r}: {inside.parts[0]!r} is protected")
    return target


def reset_workspace(root: str | Path, artifact_scope: list[str],
                    fixtures: dict[str, str] | None) -> list[Path]:
    root = Path(root).resolve()
    files = [(_inside(root, rel, "fixture"), content) for rel, content in (fixtures or {}).items()]
    cleared: list[Path] = []
    for rel in artifact_scope or []:
        target = _inside(root, rel, "artifact_scope")
        if target.exists():
            _empty(target)
            cleared.append(target)
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return cleared


def _empty(target: Path, attempts: int = 5) -> None:
    for path in sorted(target.rglob("*"), key=lambda q: -len(q.parts)):
        if path.is_dir():
            continue
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                path.unlink()
                break
            except PermissionError as e:
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
