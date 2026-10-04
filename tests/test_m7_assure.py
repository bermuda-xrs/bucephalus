"""M7 acceptance — the ratchet, the observation invariant, and the leak test."""

from __future__ import annotations


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


def test_json_schema_verifier_reports_the_real_error(ctx, task):
    task.contract.success_criterion = "json_schema"
    task.contract.output_schema = {
        "type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}
    }
    r = ctx.mesh.verify(task, '{"m": 1}')
    assert not r.passed and "missing required key" in r.observation
    r2 = ctx.mesh.verify(task, '{"n": 1}')
    assert r2.passed
