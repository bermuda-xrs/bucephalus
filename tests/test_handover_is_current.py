"""The handover, checked against the repository it describes.

This project's documented failure mode is stale documentation, three times over:
HANDOVER's "expect 326 passed" long after the suite was 790, RELEASE's demo
checkbox ticked while `demo.py` exited 1, and README's step-1 commands missing
the `--suite` that became required. Each was found by running the thing the
document described, which is the only way a prose file ever gets checked.

So `docs/HANDOVER.md` is checked here like any other artefact. The rules it is
held to are the ones that made those three defects possible:

  * a path it names must exist;
  * a command in its quickstart must be a real entry point;
  * the commit it says it was written against must be an ancestor of HEAD;
  * a document and section it points at must exist and contain that section;
  * a number that changes weekly must NOT be written down - the file must give
    the command that regenerates it instead. The best defence against a stale
    number is not writing it.

The last rule is why there is no "the test count matches a fresh run" test: the
handover states no test count, and `test_the_volatile_numbers_are_not_written_down`
fails if someone adds one.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HANDOVER = ROOT / "docs" / "HANDOVER.md"
TEXT = HANDOVER.read_text(encoding="utf-8")
# Prose wraps, so phrase checks run against a whitespace-flattened copy.
FLAT = re.sub(r"\s+", " ", TEXT)

# A token is path-like if it starts with a name character and either contains a
# directory separator or carries an extension this repository actually uses.
# `.partial.jsonl` and `.ps1` (spoken of as a suffix) start with a dot and are
# excluded; `build/` and `runs/` are real directories and are not.
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
    """A renamed or deleted file must break the handover, not outlive it."""
    named = _paths_named()
    assert named, "the path scan found nothing - the regex has stopped matching"
    missing = [p for p in named if not (ROOT / p).exists()]
    assert not missing, f"handover names paths that do not exist: {missing}"


def test_the_quickstart_commands_are_real_entry_points():
    """B2 is the first thing a newcomer runs. Two front doors were once dead."""
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
    """B3 tells a reader what a command costs before they start a 4-hour one."""
    table = TEXT.split("## B3.", 1)[1].split("\n## ", 1)[0]
    scripts = {
        t for span in _backtick_spans(table) for t in span.split() if t.endswith(".py")
    }
    assert scripts, "the cost table names no commands"
    missing = [s for s in scripts if not (ROOT / s).exists()]
    assert not missing, f"the cost table prices things that do not exist: {missing}"


def test_the_cli_subcommand_count_is_the_real_one():
    """D39's whole point is a number of unswept rooms. If the CLI grows one and
    the handover still says 25, the entry understates its own risk."""
    from agent.cli import build_parser  # noqa: PLC0415 - import cost is the test's

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


def test_every_named_alpha_subcommand_exists():
    from agent.cli import build_parser  # noqa: PLC0415

    import argparse

    choices = set(
        next(
            a for a in build_parser()._actions  # noqa: SLF001
            if isinstance(a, argparse._SubParsersAction)  # noqa: SLF001
        ).choices
    )
    named = set(re.findall(r"`alpha ([a-z-]+)", TEXT))
    unknown = named - choices
    assert not unknown, f"handover names CLI subcommands that do not exist: {unknown}"


def test_the_stated_commit_is_an_ancestor_of_head():
    """Section D pins the commit the file was verified against. If the file is
    older than the history it claims, that is the staleness this test exists for."""
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


# (document, how a reference to section N is written in it)
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
    assert refs, f"the handover no longer points into {doc}"
    missing = [r for r in refs if not re.search(anchor(r), body, re.MULTILINE)]
    assert not missing, f"{doc} has no section for {missing}"


def test_the_release_section_the_handover_names_exists():
    body = (ROOT / "RELEASE.md").read_text(encoding="utf-8")
    m = re.search(r'`RELEASE\.md`, "([^"]+)"', TEXT)
    assert m, "section D no longer points at a named section of RELEASE.md"
    assert m.group(1) in body, f"RELEASE.md has no section {m.group(1)!r}"


def test_the_volatile_numbers_are_not_written_down():
    """Prefer 'do not state it, give the command'. A test count, an open-defect
    count or a commit count in prose is a stale number waiting to happen."""
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
    """Length is a bug here: a section that grows past this is duplicating
    something that another document already owns."""
    lines = len(TEXT.splitlines())
    assert lines < 400, f"docs/HANDOVER.md is {lines} lines; the budget is under 400"


def test_the_posix_claim_is_still_true():
    """B2 and C4 tell a reader where the POSIX limits have executed. If that
    changes and the handover does not, the claim becomes a lie about the
    security posture."""
    assert "POSIX has run twice, in WSL2" in FLAT
    assert "darwin.py`) has never executed anywhere" in FLAT


if __name__ == "__main__":  # pragma: no cover - convenience
    sys.exit(pytest.main([__file__, "-q"]))
