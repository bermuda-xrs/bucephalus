"""M7.2 / M7.3 / M7.4 — the ratchet, strategy selection, and anti-gaming.

Two failure modes the ratchet prevents: the random walk downhill that makes
"self-correction degrades performance" true in the literature, and burning a
fixed N iterations on something already correct.

Anti-gaming is mandatory, not optional. Models reliably learn to exploit
whatever a verifier fails to enforce — not from deviousness, just from
optimizing for what is measured.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from ..kernel.types import TaskContract, VerifyResult
from .verifiers import Verifier, default_verifiers


class GamingSuspicion(RuntimeError):
    pass


@dataclass
class HoldoutRegistry:
    """Held-out checks NEVER enter any prompt. Ever. This is the whole point.

    Keeping them in a separate structure from anything prompt-facing makes the
    leak test (M7 acceptance) a real test rather than a hope.
    """

    _holdout: dict[str, list[str]] = field(default_factory=dict)

    def store(self, skill_id: str, checks: list[str]) -> None:
        self._holdout[skill_id] = list(checks)

    def get(self, skill_id: str) -> list[str]:
        return list(self._holdout.get(skill_id, []))

    def contains_any(self, text: str) -> list[str]:
        """Used by the leak test to grep prompts."""
        hits = []
        for skill_id, checks in self._holdout.items():
            for c in checks:
                if c and c in text:
                    hits.append(f"{skill_id}:{c[:40]}")
        return hits


def partition_tests(tests: list[str], holdout_frac: float = 0.30, seed: int = 0) -> tuple[list[str], list[str]]:
    """70/30. The drafter sees only the 70%."""
    items = list(tests)
    rng = random.Random(seed)
    rng.shuffle(items)
    n_hold = max(1, int(len(items) * holdout_frac)) if items else 0
    return items[n_hold:], items[:n_hold]


def choose_strategy(first_score: float, sample_variance: float) -> str:
    """Easy problems benefit from sequential revision; hard ones need fresh samples.

    Revising a wrong approach only polishes it.
    """
    if first_score > 0.7:
        return "sequential"  # close; refine
    if sample_variance > 0.3:
        return "parallel"  # lost; resample
    return "hybrid"  # k samples, then refine best


class VerificationMesh:
    def __init__(self, ctx, registry: dict[str, Verifier] | None = None):
        self.ctx = ctx
        self.registry: dict[str, Verifier] = registry or default_verifiers(ctx)
        self.holdouts = HoldoutRegistry()

    def add(self, name: str, verifier: Verifier) -> None:
        self.registry[name] = verifier

    def verify(self, task, artifact: str) -> VerifyResult:
        name = task.contract.success_criterion
        verifier = self.registry.get(name)
        self.ctx.log.append(task.id, "verify.attempted", {"verifier": name})
        if verifier is None:
            r = VerifyResult(
                passed=False, score=0.0,
                observation=f"no verifier registered under {name!r}",
                verifier_id="missing",
            )
        else:
            try:
                r = verifier.check(artifact, task.contract)
            except Exception as e:  # noqa: BLE001 - a broken verifier is a failed check
                r = VerifyResult(
                    passed=False, score=0.0,
                    observation=f"verifier {name} raised: {type(e).__name__}: {e}",
                    verifier_id=name,
                )
        self.ctx.log.append(
            task.id,
            "verify.passed" if r.passed else "verify.failed",
            {"verifier": r.verifier_id, "score": round(r.score, 4)},
        )
        return r

    def verify_isomorphic(self, task, artifact: str, second: str) -> tuple[VerifyResult, str | None]:
        """M7.4 rule 3 — verify the same property two independent ways.

        Disagreement is itself a finding, not something to average away.
        """
        a = self.verify(task, artifact)
        other = self.registry.get(second)
        if other is None:
            return a, None
        b = other.check(artifact, task.contract)
        if a.passed != b.passed:
            return a, (
                f"isomorphic disagreement: {a.verifier_id}={a.passed} "
                f"vs {b.verifier_id}={b.passed}"
            )
        return a, None

    def suspicion_signal(self, visible_pass: float, holdout_pass: float) -> str | None:
        """M7.4 rule 4 — a big visible/held-out gap is the signature of gaming."""
        if visible_pass - holdout_pass > 0.25:
            return (
                f"visible pass rate {visible_pass:.2f} far exceeds held-out "
                f"{holdout_pass:.2f} — suspect the artifact is fitted to the visible checks"
            )
        return None


def assert_different_authors(drafter_backend: str, test_author_backend: str) -> None:
    """M7.4 rule 1 — whoever drafts never writes the tests. Enforced in code."""
    if drafter_backend == test_author_backend:
        raise GamingSuspicion(
            "Drafter and test author must differ (anti-gaming invariant)"
        )


def refine_with_ratchet(
    ctx,
    task,
    initial: str,
    produce_revision: Callable[[str, str], str],
) -> tuple[str, VerifyResult]:
    """A revision is kept only if it scores strictly better.

    INVARIANT: every candidate is produced from a NEW external observation.
    produce_revision is called with (best_so_far, observation) and the
    observation is asserted non-empty — no thinking-harder loops.
    """
    best, best_r = initial, ctx.mesh.verify(task, initial)
    if best_r.passed:
        return best, best_r

    history: list[float] = [best_r.score]
    for i in range(ctx.config.limits.max_refinement_iterations):
        observation = best_r.observation
        if not observation.strip():
            raise AssertionError(
                "refinement attempted with an empty observation — invariant #2"
            )
        candidate = produce_revision(best, observation)
        r = ctx.mesh.verify(task, candidate)

        if r.score > best_r.score:  # strictly better only
            best, best_r = candidate, r
        # otherwise DISCARD — never accept a regression

        history.append(best_r.score)
        if best_r.passed:
            break
        if i >= 2 and _no_improvement(history):
            break  # plateau; stop burning tokens
    return best, best_r


def _no_improvement(history: list[float], window: int = 3) -> bool:
    if len(history) < window:
        return False
    tail = history[-window:]
    return max(tail) - min(tail) < 1e-9
