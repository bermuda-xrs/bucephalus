"""M9.4 — retrieval. A separate failure mode from skill quality.

Deliberately simple and inspectable: lexical overlap between the task goal and
the skill's declared applicability. A learned retriever would be harder to
debug, and "did we pick the right one" has to be measurable on its own.
"""

from __future__ import annotations

import re

MAX_SKILLS_IN_PREFIX = 3  # each one costs prefix tokens on every single call

_WORD = re.compile(r"\w{3,}")
_STOP = frozenset(
    "the and for with that this from into your you are was were will not but "
    "have has had can should would when what which who how why all any use "
    "using used make makes made".split()
)


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)} - _STOP


def score(goal: str, skill: dict) -> float:
    applies = str(skill.get("contract", {}).get("applies_when", ""))
    hay = _terms(f"{skill.get('name', '')} {applies}")
    need = _terms(goal)
    if not hay or not need:
        return 0.0
    return len(hay & need) / len(need)


def retrieve(ctx, goal: str, k: int = MAX_SKILLS_IN_PREFIX, threshold: float = 0.12) -> list[dict]:
    """Promoted skills only. Candidates and demoted skills never reach a prompt."""
    scored = [(score(goal, s), s) for s in ctx.skills.promoted()]
    chosen = [s for sc, s in sorted(scored, key=lambda x: -x[0]) if sc >= threshold]
    return chosen[:k]
