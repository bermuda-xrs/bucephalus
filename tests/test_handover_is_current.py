from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HANDOVER = ROOT / "docs" / "HANDOVER.md"
TEXT = HANDOVER.read_text(encoding="utf-8")
FLAT = re.sub(r"\s+", " ", TEXT)

PATHISH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*(?:\.(?:py|md|ps1|yaml|toml)|/)$")


def _backtick_spans(text: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", text)


def _fenced(text: str, lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", text, re.DOTALL)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def _paths_named() -> list[str]:
    out = []
    for span in _backtick_spans(TEXT):
        for token in span.split():
            if PATHISH.match(token):
                out.append(token)
    return sorted(set(out))


def test_every_path_the_handover_names_exists():
    named = _paths_named()
    assert named, "the path scan found nothing - the regex has stopped matching"
    missing = [p for p in named if not (ROOT / p).exists()]
    assert not missing, f"handover names paths that do not exist: {missing}"


def test_the_quickstart_commands_are_real_entry_points():
    blocks = _fenced(TEXT, "powershell")
    assert blocks, "the quickstart's powershell block has gone"
    ran = []
    for line in blocks[0].splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for token in line.split():
            token = token.strip('"')
            if token.startswith(".\\") or token.endswith(".py"):
                ran.append(token.removeprefix(".\\"))
    assert ran, "the quickstart block runs nothing"
    missing = [c for c in ran if not (ROOT / c).exists()]
    assert not missing, f"quickstart runs things that are not here: {missing}"
    assert "setup.ps1" in ran and "demo.py" in ran, (
        "the quickstart no longer sets up the environment and proves the pipeline"
    )


def test_the_commands_in_the_cost_table_are_real_entry_points():
    table = TEXT.split("## B3.", 1)[1].split("\n## ", 1)[0]
    scripts = {
        t for span in _backtick_spans(table) for t in span.split() if t.endswith(".py")
    }
    assert scripts, "the cost table names no commands"
    missing = [s for s in scripts if not (ROOT / s).exists()]
    assert not missing, f"the cost table prices things that do not exist: {missing}"


def test_the_cli_subcommand_count_is_the_real_one():
    from agent.cli import build_parser  # noqa: PLC0415

    import argparse

    subs = [
        a
        for a in build_parser()._actions
        if isinstance(a, argparse._SubParsersAction)  # noqa: SLF001
    ]
    assert len(subs) == 1, "the CLI no longer has exactly one subparser group"
    real = len(subs[0].choices)
    stated = {int(n) for n in re.findall(r"(\d+) subcommands", TEXT)}
    assert stated, "the handover no longer states how many subcommands are unswept"
    assert stated == {real}, f"handover says {stated} subcommands, the parser has {real}"


def test_every_named_bucephalus_subcommand_exists():
    from agent.cli import build_parser  # noqa: PLC0415

    import argparse

    choices = set(
        next(
            a for a in build_parser()._actions  # noqa: SLF001
            if isinstance(a, argparse._SubParsersAction)  # noqa: SLF001
        ).choices
    )
    named = set(re.findall(r"`bucephalus ([a-z-]+)", TEXT))
    unknown = named - choices
    assert not unknown, f"handover names CLI subcommands that do not exist: {unknown}"


def test_the_stated_commit_is_an_ancestor_of_head():
    m = re.search(r"against commit `([0-9a-f]{7,40})`", TEXT)
    assert m, "section D no longer says which commit the handover was verified against"
    sha = m.group(1)
    known = _git("cat-file", "-e", f"{sha}^{{commit}}")
    assert known.returncode == 0, f"handover names commit {sha}, which is not in this repo"
    anc = _git("merge-base", "--is-ancestor", sha, "HEAD")
    assert anc.returncode == 0, (
        f"commit {sha} is not an ancestor of HEAD - the handover describes a "
        "history this checkout does not have"
    )


SECTION_ANCHORS = {
    "docs/FINDINGS.md": (r"FINDINGS (\d+\.\d+)", lambda n: rf"^#+ {re.escape(n)} "),
    "docs/DEFERRED.md": (r"\bD(\d+)\b", lambda n: rf"^## D{n}\b"),
    "INVARIANTS.md": (r"\b(DR\d)\b", lambda n: rf"^\*\*{n}\."),
}


@pytest.mark.parametrize("doc", sorted(SECTION_ANCHORS))
def test_every_section_the_handover_points_at_exists(doc: str):
    path = ROOT / doc
    assert path.exists(), f"handover points at {doc}, which is not here"
    body = path.read_text(encoding="utf-8")
    pattern, anchor = SECTION_ANCHORS[doc]
    refs = sorted(set(re.findall(pattern, TEXT)))
    missing = [r for r in refs if not re.search(anchor(r), body, re.MULTILINE)]
    assert not missing, f"{doc} has no section for {missing}"


def test_the_release_section_the_handover_names_exists():
    body = (ROOT / "RELEASE.md").read_text(encoding="utf-8")
    m = re.search(r'`RELEASE\.md`, "([^"]+)"', TEXT)
    assert m, "section D no longer points at a named section of RELEASE.md"
    assert m.group(1) in body, f"RELEASE.md has no section {m.group(1)!r}"


def test_the_volatile_numbers_are_not_written_down():
    offenders = re.findall(r"\b\d[\d,]*\s+(?:passed|skipped|tests|open defects)\b", TEXT)
    assert not offenders, (
        f"the handover states volatile counts in prose: {offenders}. "
        "State the command that regenerates them instead (section D)."
    )
    assert "python -m pytest tests -q" in FLAT, (
        "section D must give the command that regenerates the test count"
    )


@pytest.mark.parametrize("const", ["HARNESS_VERSION", "TOOLSET_VERSION"])
def test_the_stated_versions_match_the_source(const: str):
    src = (ROOT / "src" / "agent" / "__init__.py").read_text(encoding="utf-8")
    real = re.search(rf"^{const} = (\d+)", src, re.MULTILINE)
    assert real, f"{const} is no longer defined where the handover says to grep"
    row = next(
        (ln for ln in TEXT.splitlines() if f"grep {const}" in ln),
        None,
    )
    assert row, f"section D no longer states {const}"
    stated = re.search(r"\*\*(\d+)\*\*", row)
    assert stated, f"section D's {const} row no longer states a value"
    assert stated.group(1) == real.group(1), (
        f"handover says {const} is {stated.group(1)}, source says {real.group(1)}"
    )


def test_the_handover_stays_short():
    lines = len(TEXT.splitlines())
    assert lines < 400, f"docs/HANDOVER.md is {lines} lines; the budget is under 400"


def test_the_posix_claim_is_still_true():
    assert "POSIX has run four times, in WSL2" in FLAT
    assert "darwin.py`) has never executed anywhere" in FLAT


if __name__ == "__main__":  # pragma: no cover - convenience
    sys.exit(pytest.main([__file__, "-q"]))
