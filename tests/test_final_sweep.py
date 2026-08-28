"""A task that did the work is graded, even if it never said it was done.

Verification used to run only inside the `<final>` branch. A task that wrote
a correct `output/answer.json` and then ran out of steps was recorded as a
failure and never graded at all.

Measured on granite4.1:8b, E01: the file on disk held 7672.598648385013
against an expected 7672.6 - correct to eleven significant figures - after
nine steps with no `<final>`, scored `budget_exhausted`. The suite was
measuring whether the model announces completion, not whether the work is
done.

This RAISES pass rates, so the change is deliberately visible: a pass found
this way is `done_unannounced`, never plain `done`.
"""

from __future__ import annotations

import json

import pytest

from agent.skills.gate import TaskSpec, fetch_paired_outcomes, run_paired_trial

VERIFY = '''\
import json, pathlib


def check():
    p = pathlib.Path("output/answer.json")
    if not p.exists():
        return False, 0.0
    return json.loads(p.read_text(encoding="utf-8")).get("x") == 42, 1.0
'''


def _spec(key):
    return TaskSpec(
        key=key, goal=f"write work/{key}/output/answer.json with x=42",
        success_criterion="verify_program", artifact_scope=[f"work/{key}"],
        grader_dir=f"grading/{key}",
        fixtures={f"grading/{key}/verify.py": VERIFY,
                  f"work/{key}/output/.keep": ""},
    )


def _write_code(key):
    return ("import json,pathlib\n"
            f"p=pathlib.Path('work/{key}/output/answer.json')\n"
            "p.parent.mkdir(parents=True,exist_ok=True)\n"
            "p.write_text(json.dumps({'x':42}))\n")


def test_correct_work_without_a_final_still_passes(ctx, workspace, tmp_path, monkeypatch):
    """The defect: the model does the work and never announces it."""
    monkeypatch.chdir(tmp_path)
    ctx.config.autonomy.auto_review = False          # as noise.py runs
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.limits.max_steps = 3
    spec = _spec("S1")

    # never emits <final>: writes the answer, then keeps making tool calls
    code = json.dumps({"kind": "code", "code": _write_code("S1")})
    for name in ("cloud", "local", "review"):
        ctx.backends[name]._responder = lambda p, t: code  # noqa: SLF001

    sid = ctx.skills.store("probe", "# p\n", {"applies_when": "x"},
                           {"drafted_by": "test"})
    run_paired_trial(ctx, sid, spec, seed=0)

    _, control, skill = fetch_paired_outcomes(ctx.conn, sid)[0]
    assert (control, skill) == (1, 1), (
        "a task that wrote the correct answer was scored as a failure because "
        "the model never said <final>")


def test_the_unannounced_pass_is_labelled_not_disguised(ctx, workspace, tmp_path, monkeypatch):
    """It must be countable separately: failing to terminate is a real
    deficiency even when the artifact is right."""
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract
    from agent.workspace import reset_workspace

    monkeypatch.chdir(tmp_path)
    ctx.config.autonomy.auto_review = False          # as noise.py runs
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.limits.max_steps = 3
    spec = _spec("S2")
    reset_workspace(ctx.config.root, spec.artifact_scope, spec.fixtures)

    code = json.dumps({"kind": "code", "code": _write_code("S2")})
    for name in ("cloud", "local", "review"):
        ctx.backends[name]._responder = lambda p, t: code  # noqa: SLF001

    t = create_task(ctx.conn, ctx.log, TaskContract(
        goal=spec.goal, success_criterion="verify_program",
        artifact_scope=spec.artifact_scope, grader_dir=spec.grader_dir),
        Budget(usd=1.0, seconds=600))
    res = run_task(ctx, t.id)
    assert res.status == "done_unannounced", res.status

    kinds = [r[0] for r in ctx.conn.execute(
        "SELECT type FROM events WHERE task_id=?", (t.id,))]
    assert "verify.unannounced_pass" in kinds, "the sweep must leave a record"


def test_wrong_work_without_a_final_still_fails(ctx, workspace, tmp_path, monkeypatch):
    """The sweep grades the artifact - it does not hand out passes."""
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract
    from agent.workspace import reset_workspace

    monkeypatch.chdir(tmp_path)
    ctx.config.autonomy.auto_review = False          # as noise.py runs
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.limits.max_steps = 2
    spec = _spec("S3")
    reset_workspace(ctx.config.root, spec.artifact_scope, spec.fixtures)

    wrong = ("import json,pathlib\n"
             "p=pathlib.Path('work/S3/output/answer.json')\n"
             "p.parent.mkdir(parents=True,exist_ok=True)\n"
             "p.write_text(json.dumps({'x':9999}))\n")
    code = json.dumps({"kind": "code", "code": wrong})
    for name in ("cloud", "local", "review"):
        ctx.backends[name]._responder = lambda p, t: code  # noqa: SLF001

    t = create_task(ctx.conn, ctx.log, TaskContract(
        goal=spec.goal, success_criterion="verify_program",
        artifact_scope=spec.artifact_scope, grader_dir=spec.grader_dir),
        Budget(usd=1.0, seconds=600))
    assert run_task(ctx, t.id).status == "max_steps"


def test_a_task_that_produced_nothing_still_fails(ctx, workspace, tmp_path, monkeypatch):
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract
    from agent.workspace import reset_workspace

    monkeypatch.chdir(tmp_path)
    ctx.config.autonomy.auto_review = False          # as noise.py runs
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.limits.max_steps = 2
    spec = _spec("S4")
    reset_workspace(ctx.config.root, spec.artifact_scope, spec.fixtures)
    for name in ("cloud", "local", "review"):
        ctx.backends[name]._responder = lambda p, t: json.dumps(  # noqa: SLF001
            {"kind": "action", "tool": "list_dir", "args": {"path": "work/S4"}})

    t = create_task(ctx.conn, ctx.log, TaskContract(
        goal=spec.goal, success_criterion="verify_program",
        artifact_scope=spec.artifact_scope, grader_dir=spec.grader_dir),
        Budget(usd=1.0, seconds=600))
    assert run_task(ctx, t.id).status == "max_steps"


def test_the_sweep_does_not_credit_a_verifier_that_cannot_fail(ctx, workspace, scripted):
    """`always_pass` grades nothing, so sweeping it would turn every budget
    exhaustion into a pass - exactly the free credit this must not create."""
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    task = create_task(ctx.conn, ctx.log,
                       TaskContract(goal="spend", artifact_scope=["work"]),
                       Budget(usd=0.001, seconds=600))
    assert task.contract.success_criterion == "always_pass"
    scripted("cloud", *['<action tool="list_dir">{"path": "work"}</action>'] * 6)
    assert run_task(ctx, task.id).status == "budget_exhausted"
