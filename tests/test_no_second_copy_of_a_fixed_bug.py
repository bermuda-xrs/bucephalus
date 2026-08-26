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
