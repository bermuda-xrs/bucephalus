from __future__ import annotations

from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract


def _task(ctx, criterion="always_pass"):
    return create_task(ctx.conn, ctx.log,
                       TaskContract(goal="g", artifact_scope=["work"],
                                    success_criterion=criterion),
                       Budget(usd=1.0, seconds=60))


def test_a_criterion_with_no_verifier_fails(ctx):
    t = _task(ctx, "no_such_verifier")
    r = ctx.mesh.verify(t, "anything")
    assert r.passed is False and r.verifier_id == "missing"


def test_a_verifier_that_raises_fails(ctx):
    class Boom:
        def check(self, artifact, contract, root):
            raise RuntimeError("broken grader")

    ctx.mesh.add("boom", Boom())
    r = ctx.mesh.verify(_task(ctx, "boom"), "anything")
    assert r.passed is False and "raised" in r.observation


def test_the_reviewer_never_sees_a_held_out_score(ctx):
    t = _task(ctx)
    ctx.log.append(t.id, "verify.failed",
                   {"verifier": "v", "score": 0.4375, "detail": "expected 4825, got 0"})
    ctx.config.grader_held_out = False
    shown = ctx.compact_transcript(t.id)
    assert "0.4375" in shown and "4825" not in shown
    ctx.config.grader_held_out = True
    shown = ctx.compact_transcript(t.id)
    assert "0.4375" not in shown and "4825" not in shown and "verify.failed" in shown


def test_grading_keys_are_stripped_inside_lists_too():
    from agent.ledger.prefix import prompt_contract

    got = prompt_contract({"goal": "g", "output_schema": {
        "anyOf": [{"type": "integer", "x-expected": 4825}, {"type": "null"}]}})
    assert got["output_schema"] == {"anyOf": [{"type": "integer"}, {"type": "null"}]}


def test_no_task_ids_wrote_nothing(ctx):
    from agent.kernel.scope import wrote_artifact

    assert wrote_artifact(ctx.conn, set(), "a" * 64) is False
