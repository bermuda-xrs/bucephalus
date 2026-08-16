"""M7 acceptance — the ratchet, the observation invariant, and the leak test."""

from __future__ import annotations

import pytest

from agent.assure.mesh import (
    GamingSuspicion,
    VerificationMesh,
    assert_different_authors,
    choose_strategy,
    partition_tests,
    refine_with_ratchet,
)
from agent.kernel.types import TaskContract, VerifyResult


class ScriptedVerifier:
    kind = "scripted"

    def __init__(self, scores: list[float]):
        self.scores = list(scores)
        self.i = 0

    def check(self, artifact: str, contract: TaskContract) -> VerifyResult:
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

        def check(self, artifact, contract):
            return VerifyResult(passed=False, score=0.0, observation="", verifier_id="silent")

    ctx.mesh.registry["silent"] = SilentVerifier()
    task.contract.success_criterion = "silent"
    with pytest.raises(AssertionError, match="invariant #2"):
        refine_with_ratchet(ctx, task, "x", lambda b, o: "y")


def test_different_author_rule():
    assert_different_authors("local", "cloud")
    with pytest.raises(GamingSuspicion):
        assert_different_authors("cloud", "cloud")


def test_partition_is_seventy_thirty():
    checks = [f"check {i}" for i in range(10)]
    visible, holdout = partition_tests(checks, holdout_frac=0.30)
    assert len(holdout) == 3 and len(visible) == 7
    assert not set(visible) & set(holdout)


def test_holdout_never_enters_prompt(ctx, task, workspace, scripted):
    """Scan every prompt sent during a run for held-out check content."""
    from agent.execute.loop import run_task

    secret = "HOLDOUT_CANARY_the_output_must_not_hardcode_the_example"
    ctx.mesh.holdouts.store("skill_x", [secret])

    scripted("cloud", '<final>{"answer": "ok"}</final>')
    run_task(ctx, task.id)

    for backend in ctx.backends.values():
        for prompt in getattr(backend, "prompts", []):
            assert not ctx.mesh.holdouts.contains_any(prompt), (
                "held-out check content leaked into a prompt"
            )


def test_suspicion_signal_flags_the_gap(ctx):
    mesh: VerificationMesh = ctx.mesh
    assert mesh.suspicion_signal(0.95, 0.40) is not None
    assert mesh.suspicion_signal(0.80, 0.75) is None


def test_isomorphic_disagreement_is_a_finding(ctx, task):
    ctx.mesh.registry["a"] = ScriptedVerifier([1.0])
    ctx.mesh.registry["b"] = ScriptedVerifier([0.0])
    task.contract.success_criterion = "a"
    _, finding = ctx.mesh.verify_isomorphic(task, "artifact", "b")
    assert finding and "disagreement" in finding


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
