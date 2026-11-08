from __future__ import annotations

from pathlib import Path

FROZEN = {"FINDINGS.md", "SPEC-DELTA.md", "INVARIANTS.md", "RELEASE.md", "PLATFORMS.md"}
LIVE = {"HANDOVER.md", "DEFERRED.md", "CHANGELOG.md"}


def source_root() -> Path | None:
    root = Path(__file__).resolve().parents[3]
    return root if (root / "pyproject.toml").is_file() and (root / "docs").is_dir() else None


def files(root: Path | None) -> dict[str, Path]:
    if root is None:
        return {}
    return {p.name: p for p in [*sorted((root / "docs").glob("*.md")), *sorted(root.glob("*.md"))] if p.is_file()}


def kind(name: str) -> str:
    return "frozen" if name in FROZEN or name.startswith("AUDIT") else "live" if name in LIVE else ""


def listing(root: Path | None) -> list[dict]:
    out = []
    for name, p in files(root).items():
        text = p.read_text(encoding="utf-8", errors="replace")
        out.append({"name": name, "where": "docs" if p.parent.name == "docs" else "", "kind": kind(name),
                    "title": next((x[2:].strip() for x in text.splitlines() if x.startswith("# ")), p.stem),
                    "bytes": p.stat().st_size})
    return sorted(out, key=lambda d: (d["name"] != "README.md", d["where"], d["name"]))
