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
            r = VerifyResult(
                passed=False, score=0.0,
                observation=f"{name} inspects nothing, so under measurement it "
                            "cannot grade - give the task a real criterion",
                verifier_id=name,
            )
        else:
            try:
                kw = {"root": self.ctx.config.root}
                if "task" in inspect.signature(verifier.check).parameters:
                    kw["task"] = task
                r = verifier.check(artifact, task.contract, **kw)
            except Exception as e:  # noqa: BLE001
                r = VerifyResult(
                    passed=False, score=0.0,
                    observation=f"verifier {name} raised: {type(e).__name__}: {e}",
                    verifier_id=name,
                )
        self.ctx.log.append(
            task.id,
            "verify.passed" if r.passed else "verify.failed",
            {"verifier": r.verifier_id, "score": round(r.score, 4),
             "detail": r.observation},
        )
        return r

    def feedback_for_model(self, r: VerifyResult) -> str:
        if getattr(self.ctx.config, "grader_held_out", False):
            return ("Verification failed. The grader is held out under "
                    "measurement, so you are told only that the answer did "
                    "not pass - not why. Check your work against the task "
                    "and its inputs.")
        return (f"Verification failed ({r.verifier_id}, "
                f"score {r.score:.2f}):\n{r.observation}")
