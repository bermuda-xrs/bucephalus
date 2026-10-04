"""The isolation fix, exercised through the real paired-trial pipeline.

`tests/test_workspace_isolation.py` tests `reset_workspace` directly. That is
not the same as testing that the pipeline CALLS it — the exact distinction
that let seven components in this project sit dead with green tests, and the
reason `tests/test_wired_invariants.py` exists.

So these drive `run_paired_trial` itself: a real `AgentContext`, the real
`run_task` loop, the real `verify_program` verifier, and a mock backend that
answers `<final>` without ever writing an output file.

That last detail is what makes the test decisive. If a correct
`output/answer.json` is planted before the trial and nothing resets the
workspace, `verify_program` grades the planted file and BOTH arms pass — a
fabricated result from a model that did no work. If the reset fires, the
planted file is gone and both arms fail honestly.

NOTE ON CWD. `verify_program` resolves `artifact_scope` against the PROCESS
working directory, not `config.root`. The bench harnesses run from the project
root so this works there, but a test whose workspace is under `tmp_path` will
silently find no `verify.py`, fail every trial, and make these assertions hold
vacuously. Hence `monkeypatch.chdir` in every test below - without it they
were green and proved nothing.
"""

from __future__ import annotations

import json


from agent.skills.gate import TaskSpec, fetch_paired_outcomes, run_paired_trial

# Passes only if output/answer.json holds value == 42.
VERIFY = '''\
import json, pathlib


def check():
    p = pathlib.Path("output/answer.json")
    if not p.exists():
        return False, 0.0
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False, 0.0
    return (got.get("value") == 42), (1.0 if got.get("value") == 42 else 0.0)
'''

CORRECT_ANSWER = json.dumps({"value": 42})


def _spec(key: str = "T01") -> TaskSpec:
    return TaskSpec(
        key=key,
        goal=f"Write work/{key}/output/answer.json containing value 42.",
        success_criterion="verify_program",
        artifact_scope=[f"work/{key}"],
        fixtures={
            f"work/{key}/verify.py": VERIFY,
            f"work/{key}/input/params.json": json.dumps({"target": 42}),
            f"work/{key}/output/.keep": "",
        },
    )


def _skill(ctx) -> str:
    return ctx.skills.store(
        "isolation-probe",
        "# probe\n\n## procedure\nDo the arithmetic.\n",
        {"applies_when": "anything"},
        {"drafted_by": "test", "drafted_from": []},   # declared: see D29's sweep
    )


def test_a_planted_answer_cannot_make_a_paired_trial_pass(ctx, workspace, tmp_path, monkeypatch):
    """The fabricated result this fix exists to prevent.

    The mock backend answers `<final>` and writes nothing, so the only way
    either arm can pass is by being graded against the planted file.
    """
    monkeypatch.chdir(tmp_path)
    ctx.config.limits.max_steps = 1
    spec = _spec()
    sid = _skill(ctx)

    planted = ctx.config.resolve(f"work/{spec.key}/output/answer.json")
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(CORRECT_ANSWER, encoding="utf-8")

    run_paired_trial(ctx, sid, spec, seed=0)

    pairs = fetch_paired_outcomes(ctx.conn, sid)
    assert len(pairs) == 1
    _, control, skill = pairs[0]
    assert (control, skill) == (0, 0), (
        "an arm passed without writing an answer — it was graded against the "
        "planted file, which is a fabricated result"
    )
    assert not planted.exists(), "the planted answer survived the trial"


def test_the_control_arm_does_not_inherit_the_skill_arms_output(ctx, workspace, tmp_path, monkeypatch):
    """The dangerous direction: contamination that INFLATES the measured lift.

    Both arms share one workspace. Whichever runs first leaves its answer
    behind, so without a reset the second arm is graded against the first
    arm's work — and `run_paired_trial` randomizes arm order, so this would
    corrupt results non-deterministically.
    """
    monkeypatch.chdir(tmp_path)
    ctx.config.limits.max_steps = 1
    spec = _spec("T02")
    sid = _skill(ctx)

    answer = ctx.config.resolve(f"work/{spec.key}/output/answer.json")

    # First arm "succeeds" by leaving a correct answer on disk.
    answer.parent.mkdir(parents=True, exist_ok=True)
    answer.write_text(CORRECT_ANSWER, encoding="utf-8")
    run_paired_trial(ctx, sid, spec, seed=1)

    _, control, skill = fetch_paired_outcomes(ctx.conn, sid)[0]
    assert control == skill, (
        f"the arms disagree (control={control}, skill={skill}) with a model "
        "that behaves identically in both — that is leakage, not a lift"
    )
    assert (control, skill) == (0, 0)


def test_the_fixtures_are_actually_materialized_for_the_trial(ctx, workspace, tmp_path, monkeypatch):
    """The other half: reset must REBUILD, not merely delete.

    A trial whose inputs are missing measures nothing either. TaskSpec had no
    fixtures field at all before this, so the calibration would have run
    against an empty workspace.
    """
    monkeypatch.chdir(tmp_path)
    spec = _spec("T03")
    ctx.config.limits.max_steps = 1
    run_paired_trial(ctx, _skill(ctx), spec, seed=2)

    for rel in (f"work/{spec.key}/verify.py", f"work/{spec.key}/input/params.json"):
        assert ctx.config.resolve(rel).exists(), f"{rel} was never provisioned"
    params = json.loads(
        ctx.config.resolve(f"work/{spec.key}/input/params.json").read_text(
            encoding="utf-8"))
    assert params == {"target": 42}


def test_a_task_that_corrupts_its_own_inputs_does_not_poison_the_next_arm(ctx, workspace, tmp_path, monkeypatch):
    """An agent with write access inside its scope can edit its own inputs."""
    monkeypatch.chdir(tmp_path)
    spec = _spec("T04")
    ctx.config.limits.max_steps = 1
    params = ctx.config.resolve(f"work/{spec.key}/input/params.json")
    params.parent.mkdir(parents=True, exist_ok=True)
    params.write_text('{"target": 999999}', encoding="utf-8")

    run_paired_trial(ctx, _skill(ctx), spec, seed=3)

    assert json.loads(params.read_text(encoding="utf-8")) == {"target": 42}, \
        "a corrupted input survived into the trial"


def test_the_reset_does_not_make_passing_impossible(ctx, workspace, tmp_path, monkeypatch):
    """The opposite failure: a reset so aggressive that nothing can ever pass.

    A suite where every task fails regardless measures nothing, exactly like
    one where every task passes. So: put a correct answer in the FIXTURES, so
    the reset itself materializes it, and require the trial to pass.

    What this proves is narrow and worth stating: after `reset_workspace`, the
    workspace is one in which the verifier can find and accept a correct
    answer. It does not prove the agent wrote it - the tests above already
    turn on the agent's writes being the only thing that counts.
    """
    monkeypatch.chdir(tmp_path)
    ctx.config.limits.max_steps = 1
    spec = _spec("T05")
    spec.fixtures[f"work/{spec.key}/output/answer.json"] = CORRECT_ANSWER
    sid = _skill(ctx)

    run_paired_trial(ctx, sid, spec, seed=4)

    pairs = fetch_paired_outcomes(ctx.conn, sid)
    assert pairs, "no pair was recorded"
    _, control, skill = pairs[0]
    assert (control, skill) == (1, 1), (
        "a workspace holding a correct answer must verify in both arms; got "
        f"control={control} skill={skill} - the reset is destroying the "
        "fixtures it is supposed to rebuild")


def test_the_old_behaviour_really_did_fabricate_a_pass(ctx, workspace, tmp_path, monkeypatch):
    """The negative control. A test that cannot fail is worth nothing.

    This restores the pre-fix `provision` - write the declared fixtures, delete
    nothing - and asserts the corruption actually occurs: a planted answer
    makes BOTH arms pass with a model that never wrote a file.

    If this test ever starts failing, the scenario above has stopped being
    reachable and the tests guarding it have gone vacuous.
    """
    monkeypatch.chdir(tmp_path)
    import agent.skills.gate as gate_mod

    def old_provision(root, artifact_scope, fixtures):
        """Exactly what provision() used to do: write, never delete."""
        from pathlib import Path

        for rel, content in (fixtures or {}).items():
            path = Path(root, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return []

    monkeypatch.setattr(gate_mod, "reset_workspace", old_provision)

    ctx.config.limits.max_steps = 1
    spec = _spec("T06")
    sid = _skill(ctx)

    planted = ctx.config.resolve(f"work/{spec.key}/output/answer.json")
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(CORRECT_ANSWER, encoding="utf-8")

    run_paired_trial(ctx, sid, spec, seed=5)

    _, control, skill = fetch_paired_outcomes(ctx.conn, sid)[0]
    assert (control, skill) == (1, 1), (
        "the old behaviour was supposed to fabricate a pass from the planted "
        f"answer, but gave control={control} skill={skill}. If this is no "
        "longer reachable, the isolation tests above prove nothing.")
