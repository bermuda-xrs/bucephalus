"""The grader hold-out and retrieval isolation, pinned where mutation testing
found them unpinned (iteration 3, 2.3; scripts/mutate.py, whole-suite recheck).

Each test names the mutant it kills. Before these, each change left the
whole suite green.
"""

from __future__ import annotations

from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract


def _task(ctx, criterion="always_pass"):
    return create_task(ctx.conn, ctx.log,
                       TaskContract(goal="g", artifact_scope=["work"],
                                    success_criterion=criterion),
                       Budget(usd=1.0, seconds=60))


def test_a_criterion_with_no_verifier_fails(ctx):
    """Kills the missing-verifier result's passed=False -> True: a typo in a
    task's criterion would pass every attempt."""
    t = _task(ctx, "no_such_verifier")
    r = ctx.mesh.verify(t, "anything")
    assert r.passed is False and r.verifier_id == "missing"


def test_a_verifier_that_raises_fails(ctx):
    """Kills the raised-verifier result's passed=False -> True."""
    class Boom:
        def check(self, artifact, contract, root):
            raise RuntimeError("broken grader")

    ctx.mesh.add("boom", Boom())
    r = ctx.mesh.verify(_task(ctx, "boom"), "anything")
    assert r.passed is False and "raised" in r.observation


def test_the_reviewer_never_sees_a_held_out_score(ctx):
    """Kills `if held_out:` negated in compact_transcript: the reviewer's
    reason goes back to the executor, so a held-out score it reads is a score
    the graded model reads one hop later. Not held out, the score stays."""
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
    """Kills prompt_contract's list branch returning None: `anyOf` and
    `prefixItems` hold schemas, and their `x-` keys are grading keys."""
    from agent.ledger.prefix import prompt_contract

    got = prompt_contract({"goal": "g", "output_schema": {
        "anyOf": [{"type": "integer", "x-expected": 4825}, {"type": "null"}]}})
    assert got["output_schema"] == {"anyOf": [{"type": "integer"}, {"type": "null"}]}


def test_no_task_ids_wrote_nothing(ctx):
    """Kills wrote_artifact's empty-set `return False` -> True. Isolation on
    with no task to scope to is the empty set, and must read nothing."""
    from agent.kernel.scope import wrote_artifact

    assert wrote_artifact(ctx.conn, set(), "a" * 64) is False
