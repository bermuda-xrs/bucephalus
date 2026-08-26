"""A correction applied in one place and missed in another.

CORRECTIONS.md 1.5 records that the spec's `agreement >= 0.67` with the
comment "2 of 3 agree" is wrong: two of three is 0.6667, which is LESS than
0.67, so the literal rejects the case it was written to accept. The fix
became `DEFAULT_AGREEMENT = 2 / 3` in router/cascade.py.

A second copy of the same comparison survived in assure/verifiers.py, where
the self-consistency verifier failed every 2-of-3 agreement. Found by a
dead-code audit, not by a test - so here is the test.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent.router.cascade import DEFAULT_AGREEMENT

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"


def test_two_of_three_agreement_passes_the_threshold():
    """The whole point of the correction, as arithmetic."""
    assert 2 / 3 >= DEFAULT_AGREEMENT
    assert not (2 / 3 >= 0.67), "0.67 is the wrong constant, by 0.0033"


def test_no_module_compares_agreement_against_a_hardcoded_067():
    """The constant exists so there is one place to be right."""
    offenders = []
    for f in SRC.rglob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r">=\s*0\.6[0-9]", line) and "DEFAULT_AGREEMENT" not in line:
                # a comment recording the bug is fine; a comparison is not
                if line.lstrip().startswith("#"):
                    continue
                offenders.append(f"{f.relative_to(SRC)}:{i}: {line.strip()}")
    assert not offenders, (
        "a hardcoded agreement threshold reappeared:\n  " + "\n  ".join(offenders))


def test_the_self_consistency_verifier_uses_the_shared_constant():
    body = (SRC / "assure" / "verifiers.py").read_text(encoding="utf-8")
    assert "DEFAULT_AGREEMENT" in body, \
        "verifiers.py must use the shared threshold, not its own literal"


# ---------------------------------------------------------------------------
# The rest of CORRECTIONS.md, pinned. Each of these was wrong in the original
# spec and fixed once. The agreement threshold shows what happens when a fix
# lands in one of two places, so the others are checked here by source.
# ---------------------------------------------------------------------------


def test_tiktoken_is_not_used_for_token_counting():
    """CORRECTIONS 1.2 - OpenAI's tokenizer undercounts Claude by 15-20% on
    prose and more on code, which would make every cost figure quietly wrong."""
    for f in SRC.rglob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "tiktoken" in line and not line.lstrip().startswith("#"):
                if "import" in line or "encoding_for" in line:
                    raise AssertionError(f"{f.name}:{i} uses tiktoken: {line.strip()}")


def test_the_semantic_cache_refuses_to_launder_taint():
    """CORRECTIONS 1.4 - the spec's `stored >= requested` hands
    untrusted-derived values to trusted callers, which is the laundering its
    own acceptance test forbids. The comparison must be `<=`."""
    body = (SRC / "ledger" / "semantic_cache.py").read_text(encoding="utf-8")
    assert re.search(r"Taint\(row\[1\]\)\.level\s*<=\s*taint\.level", body), \
        "the taint comparison is not `<=`; a cache hit could launder taint"


def test_compaction_preserves_relative_paths():
    """CORRECTIONS 1.6 - the spec's regex matched only absolute POSIX paths,
    so `src/parser.py` survived compaction as `/parser.py`, which is not
    'character-for-character'."""
    from agent.ledger.compact import extract_exact_strings

    kept = extract_exact_strings("see src/parser.py and ./a/b.txt for detail")
    joined = " ".join(kept)
    assert "src/parser.py" in joined, f"relative path was mangled: {kept}"


def test_the_context_ceiling_budgets_against_the_usable_window():
    """CORRECTIONS 1.10 - a fill ratio against a 1M advertised window is not a
    ceiling at all. M3.4 must budget against the usable window."""
    from agent.config import BackendConfig

    b = BackendConfig(kind="mock", model="m", context_window=1_000_000,
                      usable_context_window=16_384)
    assert b.budgeted_window == 16_384
    assert BackendConfig(kind="mock", model="m",
                         context_window=200_000).budgeted_window == 200_000
