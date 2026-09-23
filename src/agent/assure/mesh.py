"""M7.2 / M7.3 — the ratchet and strategy selection.

Two failure modes the ratchet prevents: the random walk downhill that makes
"self-correction degrades performance" true in the literature, and burning a
fixed N iterations on something already correct.

M7.4's anti-gaming rules lived here too - different authors for a skill and
its checks, a held-out registry, the visible/held-out gap, isomorphic
verification. They policed GENERATED skills, and were retired with the
generator on 2026-09-15 (FINDINGS 1.27). What guards a hand-written skill is
structural: every check is held out, because the grader is unreachable
(SPEC-DELTA C1).
"""

from __future__ import annotations

import inspect
from typing import Callable

from ..kernel.types import TaskContract, VerifyResult
from .verifiers import Verifier, default_verifiers


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
                # A verifier that calls a model needs the task, so the call
                # is on the task's record (D36). Only it is handed one.
                kw = {"root": self.ctx.config.root}
                if "task" in inspect.signature(verifier.check).parameters:
                    kw["task"] = task
                r = verifier.check(artifact, task.contract, **kw)
            except Exception as e:  # noqa: BLE001 - a broken verifier is a failed check
                r = VerifyResult(
                    passed=False, score=0.0,
                    observation=f"verifier {name} raised: {type(e).__name__}: {e}",
                    verifier_id=name,
                )
        # The FULL detail goes on the chain, for the operator. It recorded
        # only the score before, so when the model was being fed its own
        # answer key there was no record of what it had been fed. Nothing
        # that renders events for a MODEL may pass `detail` through - see
        # `AgentContext.compact_transcript`, the one such renderer.
        self.ctx.log.append(
            task.id,
            "verify.passed" if r.passed else "verify.failed",
            {"verifier": r.verifier_id, "score": round(r.score, 4),
             "detail": r.observation},
        )
        return r

    def feedback_for_model(self, r: VerifyResult) -> str:
        """What the MODEL may be told about a failed verdict. The one place.

        Held out (under measurement): that it failed. No score, no location,
        no text - because the verifier that grades the task must not also
        teach the answer, and `expected_value`'s own mismatch message is
        "$.total: expected 4825, got 0". Every kind of verifier is covered,
        not only the two that leaked: pytest and `verify_program` report the
        grader's assertion text, which is the same leak waiting for a task
        that fails the right way.

        Not held out: unchanged, detailed. That is the product behaviour, and
        it is correct - a user's own tests reporting "expected 9, got 7" is
        what the refinement loop exists to deliver.
        """
        if getattr(self.ctx.config, "grader_held_out", False):
            return ("Verification failed. The grader is held out under "
                    "measurement, so you are told only that the answer did "
                    "not pass - not why. Check your work against the task "
                    "and its inputs.")
        return (f"Verification failed ({r.verifier_id}, "
                f"score {r.score:.2f}):\n{r.observation}")



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
        # Invariant #2 is checked on the VERIFIER'S OWN output - did anything
        # external happen at all? - and only then is the verdict passed on,
        # through `feedback_for_model`, because the revision producer is a
        # model. Checking the wrapped text instead made #2 unable to fire:
        # `feedback_for_model` never returns an empty string, so a verifier
        # that said nothing sailed through (found by
        # test_no_refinement_without_observation, 2026-09-23).
        #
        # No production path calls this today, which is why it had not
        # leaked. That is disuse, not safety; the day it is wired, it holds.
        if not best_r.observation.strip():
            raise AssertionError(
                "refinement attempted with an empty observation — invariant #2"
            )
        candidate = produce_revision(best, ctx.mesh.feedback_for_model(best_r))
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
