from __future__ import annotations

import re
from pathlib import Path

from agent.router.cascade import DEFAULT_AGREEMENT

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"


def test_two_of_three_agreement_passes_the_threshold():
    assert 2 / 3 >= DEFAULT_AGREEMENT
    assert not (2 / 3 >= 0.67), "0.67 is the wrong constant, by 0.0033"


def test_no_module_compares_agreement_against_a_hardcoded_067():
    offenders = []
    for f in SRC.rglob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r">=\s*0\.6[0-9]", line) and "DEFAULT_AGREEMENT" not in line:
                if line.lstrip().startswith("#"):
                    continue
                offenders.append(f"{f.relative_to(SRC)}:{i}: {line.strip()}")
    assert not offenders, (
        "a hardcoded agreement threshold reappeared:\n  " + "\n  ".join(offenders))


def test_the_self_consistency_verifier_uses_the_shared_constant():
    body = (SRC / "assure" / "verifiers.py").read_text(encoding="utf-8")
    assert "DEFAULT_AGREEMENT" in body, \
        "verifiers.py must use the shared threshold, not its own literal"


def test_tiktoken_is_not_used_for_token_counting():
    for f in SRC.rglob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "tiktoken" in line and not line.lstrip().startswith("#"):
                if "import" in line or "encoding_for" in line:
                    raise AssertionError(f"{f.name}:{i} uses tiktoken: {line.strip()}")


def test_the_semantic_cache_refuses_to_launder_taint():
    body = (SRC / "ledger" / "semantic_cache.py").read_text(encoding="utf-8")
    assert re.search(r"Taint\(row\[1\]\)\.level\s*<=\s*taint\.level", body), \
        "the taint comparison is not `<=`; a cache hit could launder taint"


def test_compaction_preserves_relative_paths():
    from agent.ledger.compact import extract_exact_strings

    kept = extract_exact_strings("see src/parser.py and ./a/b.txt for detail")
    joined = " ".join(kept)
    assert "src/parser.py" in joined, f"relative path was mangled: {kept}"


def test_the_context_ceiling_budgets_against_the_usable_window():
    from agent.config import BackendConfig

    b = BackendConfig(kind="mock", model="m", context_window=1_000_000,
                      usable_context_window=16_384)
    assert b.budgeted_window == 16_384
    assert BackendConfig(kind="mock", model="m",
                         context_window=200_000).budgeted_window == 200_000
