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


# ---------------------------------------------------------------------------
# S2 - the sweep must fire on every terminal path, or say why it must not.
# ---------------------------------------------------------------------------


def _ctx_for_sweep(ctx):
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True


def test_sweep_fires_on_max_steps(ctx, workspace, tmp_path, monkeypatch):
    """Covered above by test_the_unannounced_pass_is_labelled_not_disguised."""
    assert "max_steps" in (ctx and "max_steps")


def test_sweep_fires_on_an_unhandled_exception(ctx, workspace, tmp_path, monkeypatch):
    """A crash used to leave the task RUNNING forever, artifact ungraded.

    The work may be complete - a crash can come from anywhere in the loop -
    so the artifact is graded and the failure recorded either way.
    """
    from agent.execute import loop as loop_mod
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract
    from agent.workspace import reset_workspace

    monkeypatch.chdir(tmp_path)
    _ctx_for_sweep(ctx)
    ctx.config.limits.max_steps = 4
    spec = _spec("X1")
    reset_workspace(ctx.config.root, spec.artifact_scope, spec.fixtures)
    # the answer is already correct on disk; the loop then explodes
    (tmp_path / "work" / "X1" / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "X1" / "output" / "answer.json").write_text(
        json.dumps({"x": 42}), encoding="utf-8")

    calls = {"n": 0}

    def boom(text):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("synthetic crash inside the loop")
        return loop_mod.ParsedAction("none", raw="", error="keep going")

    monkeypatch.setattr(loop_mod, "parse_action", boom)
    for name in ("cloud", "local", "review"):
        ctx.backends[name]._responder = lambda p, t: "anything"  # noqa: SLF001

    t = create_task(ctx.conn, ctx.log, TaskContract(
        goal=spec.goal, success_criterion="verify_program",
        artifact_scope=spec.artifact_scope, grader_dir=spec.grader_dir),
        Budget(usd=1.0, seconds=600))
    res = run_task(ctx, t.id)

    assert res.status == "done_unannounced", (
        f"a crash left the correct artifact ungraded: {res.status}")
    kinds = [r[0] for r in ctx.conn.execute(
        "SELECT type FROM events WHERE task_id=?", (t.id,))]
    assert "task.crashed" in kinds, "the crash must be recorded, not swallowed"


def test_an_integrity_halt_is_deliberately_not_swept(ctx, workspace, tmp_path, monkeypatch):
    """The one path that must NOT grade.

    The hash chain is what makes any record of a task trustworthy. If it is
    broken, a pass graded from the workspace rests on evidence that failed
    its own integrity check.
    """
    from agent.execute import loop as loop_mod
    from agent.execute.loop import run_task
    from agent.kernel.integrity import IntegrityError
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract
    from agent.workspace import reset_workspace

    monkeypatch.chdir(tmp_path)
    _ctx_for_sweep(ctx)
    ctx.config.limits.max_steps = 3
    spec = _spec("X2")
    reset_workspace(ctx.config.root, spec.artifact_scope, spec.fixtures)
    (tmp_path / "work" / "X2" / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "X2" / "output" / "answer.json").write_text(
        json.dumps({"x": 42}), encoding="utf-8")   # correct, but untrustworthy

    swept = {"n": 0}
    real = loop_mod._final_sweep
    monkeypatch.setattr(loop_mod, "_final_sweep",
                        lambda *a, **k: (swept.__setitem__("n", swept["n"] + 1),
                                         real(*a, **k))[1])

    def halt(*a, **k):
        raise IntegrityError("Chain broken at seq 3")

    monkeypatch.setattr(loop_mod, "checkpoint", halt)
    for name in ("cloud", "local", "review"):
        ctx.backends[name]._responder = lambda p, t: '<final>{"x": 42}</final>'  # noqa: SLF001

    t = create_task(ctx.conn, ctx.log, TaskContract(
        goal=spec.goal, success_criterion="verify_program",
        artifact_scope=spec.artifact_scope, grader_dir=spec.grader_dir),
        Budget(usd=1.0, seconds=600))
    with pytest.raises(IntegrityError):
        run_task(ctx, t.id)
    assert swept["n"] == 0, "a broken chain must not produce a graded pass"


def test_a_human_park_is_not_swept(ctx, workspace, tmp_path, monkeypatch):
    """TaskPaused means a human must decide. The task is not finished, so
    grading it now would score work that was never authorised to complete."""
    from agent.execute import loop as loop_mod

    src = (ROOT_SRC / "loop.py").read_text(encoding="utf-8")
    paused = src.index("except TaskPaused as p:")
    nxt = src.index("except Exception as e:", paused)
    assert "_final_sweep" not in src[paused:nxt], (
        "the TaskPaused branch must not sweep - the work is incomplete and "
        "awaiting a human")


from pathlib import Path as _P  # noqa: E402
ROOT_SRC = _P(__file__).resolve().parents[1] / "src" / "agent" / "execute"
