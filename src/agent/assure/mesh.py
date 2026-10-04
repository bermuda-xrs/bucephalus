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

from ..kernel.types import VerifyResult
from .verifiers import Verifier, default_verifiers, inspects


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
        elif (inspects(verifier) == "nothing"
              and getattr(self.ctx.config, "grader_held_out", False)):
            # DR7. A task graded by nothing is not graded, and under
            # measurement its pass would be counted as one - bench/run.py
            # defaults a task with no criterion to always_pass.
            r = VerifyResult(
                passed=False, score=0.0,
                observation=f"{name} inspects nothing, so under measurement it "
                            "cannot grade - give the task a real criterion",
                verifier_id=name,
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


