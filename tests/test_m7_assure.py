"""M7 acceptance — the ratchet, the observation invariant, and the leak test."""

from __future__ import annotations

import pytest

from agent.assure.mesh import choose_strategy, refine_with_ratchet
from agent.kernel.types import TaskContract, VerifyResult


class ScriptedVerifier:
    kind = "scripted"

    def __init__(self, scores: list[float]):
        self.scores = list(scores)
        self.i = 0

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        s = self.scores[min(self.i, len(self.scores) - 1)]
        self.i += 1
        return VerifyResult(
            passed=s >= 1.0, score=s, observation=f"scored {s}", verifier_id=self.kind
        )


def test_ratchet_never_regresses(ctx, task):
    """Scores 0.8, 0.3, 0.5, 0.9 -> the 0.9 artifact wins, the 0.3 is discarded."""
    ctx.mesh.registry["scripted"] = ScriptedVerifier([0.8, 0.3, 0.5, 0.9])
    task.contract.success_criterion = "scripted"

    produced = ["cand-0.3", "cand-0.5", "cand-0.9"]
    seen_observations: list[str] = []

    def produce_revision(best: str, observation: str) -> str:
        seen_observations.append(observation)
        # The ratchet keeps going after 0.9 because 0.9 is an improvement but
        # not a pass; it stops on the plateau, not on running out of script.
        return produced[min(len(seen_observations) - 1, len(produced) - 1)]

    best, result = refine_with_ratchet(ctx, task, "initial-0.8", produce_revision)
    assert best == "cand-0.9"
    assert result.score == 0.9
    assert all(o.strip() for o in seen_observations)


def test_ratchet_stops_when_already_passing(ctx, task):
    ctx.mesh.registry["scripted"] = ScriptedVerifier([1.0])
    task.contract.success_criterion = "scripted"
    calls = []
    best, r = refine_with_ratchet(
        ctx, task, "good", lambda b, o: calls.append(1) or "worse"
    )
    assert r.passed and best == "good" and not calls


def test_no_refinement_without_observation(ctx, task):
    """produce_revision is never called with an empty observation."""

    class SilentVerifier:
        kind = "silent"

        def check(self, artifact, contract, root=None):
            return VerifyResult(passed=False, score=0.0, observation="", verifier_id="silent")

    ctx.mesh.registry["silent"] = SilentVerifier()
    task.contract.success_criterion = "silent"
    with pytest.raises(AssertionError, match="invariant #2"):
        refine_with_ratchet(ctx, task, "x", lambda b, o: "y")


def test_choose_strategy():
    assert choose_strategy(0.9, 0.1) == "sequential"
    assert choose_strategy(0.2, 0.5) == "parallel"
    assert choose_strategy(0.2, 0.1) == "hybrid"


def test_json_schema_verifier_reports_the_real_error(ctx, task):
    task.contract.success_criterion = "json_schema"
    task.contract.output_schema = {
        "type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}
    }
    r = ctx.mesh.verify(task, '{"m": 1}')
    assert not r.passed and "missing required key" in r.observation
    r2 = ctx.mesh.verify(task, '{"n": 1}')
    assert r2.passed
