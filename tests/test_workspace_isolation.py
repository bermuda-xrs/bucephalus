"""A run must never be graded against the previous run's output.

`verify_program` reads files off disk. `provision()` only ever WROTE the
declared fixtures, so anything a previous run left behind survived —
`output/answer.json` above all. Two ways that corrupts a measurement:

  * a repeat pass of the noise floor grades run 2 against run 1's answer
    whenever run 2 produces none, so a flaky task reads as stable;
  * the two arms of a paired trial share one workspace, so the CONTROL arm
    can be graded against the answer the SKILL arm just wrote — manufacturing
    precisely the lift the gate exists to detect.

The second is the dangerous one: it inflates the measured effect in the
direction the experimenter is hoping for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.workspace import UnsafeScope, reset_workspace


def _fixtures(task="E01", answer=None):
    f = {
        f"work/{task}/input/params.json": json.dumps({"altitude_km": 400}),
        f"work/{task}/verify.py": "def check():\n    return True, 1.0\n",
        f"work/{task}/output/.keep": "",
    }
    if answer is not None:
        f[f"work/{task}/output/answer.json"] = json.dumps(answer)
    return f


def test_a_previous_answer_does_not_survive_into_the_next_run(tmp_path):
    """The core defect. A stale answer is a false pass waiting to happen."""
    stale = tmp_path / "work" / "E01" / "output" / "answer.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"v_circular_m_s": 7672.6}), encoding="utf-8")
    assert stale.exists()

    reset_workspace(tmp_path, ["work/E01"], _fixtures())

    assert not stale.exists(), "the previous run's answer survived the reset"
    assert (tmp_path / "work" / "E01" / "input" / "params.json").exists()


def test_both_arms_of_a_pair_start_from_identical_bytes(tmp_path):
    """The control arm must not inherit the skill arm's answer."""
    scope, fx = ["work/E01"], _fixtures()

    reset_workspace(tmp_path, scope, fx)                     # skill arm runs
    ans = tmp_path / "work" / "E01" / "output" / "answer.json"
    ans.write_text(json.dumps({"v_circular_m_s": 7672.6}), encoding="utf-8")

    reset_workspace(tmp_path, scope, fx)                     # control arm
    assert not ans.exists(), \
        "the control arm would have been graded against the skill arm's answer"


def test_an_edited_fixture_is_restored_not_merged(tmp_path):
    """A task that scribbles on its own inputs must not poison the next run."""
    reset_workspace(tmp_path, ["work/E01"], _fixtures())
    params = tmp_path / "work" / "E01" / "input" / "params.json"
    params.write_text('{"altitude_km": 999999}', encoding="utf-8")

    reset_workspace(tmp_path, ["work/E01"], _fixtures())
    assert json.loads(params.read_text(encoding="utf-8"))["altitude_km"] == 400


def test_it_refuses_to_delete_the_project_root(tmp_path):
    for scope in (["."], [""], ["work/../.."]):
        with pytest.raises(UnsafeScope):
            reset_workspace(tmp_path, scope, {})


def test_it_refuses_to_delete_source_or_the_anchor(tmp_path):
    """A harness that can be pointed at the wrong directory is worse than
    one that leaves stale files. `anchor/` holds the hash chain."""
    for scope in (["src"], ["tests"], ["anchor"], ["skills_store"], ["bench/tasks"]):
        with pytest.raises(UnsafeScope):
            reset_workspace(tmp_path, scope, {})


def test_it_refuses_a_scope_outside_the_project(tmp_path):
    with pytest.raises(UnsafeScope):
        reset_workspace(tmp_path / "proj", ["../../elsewhere"], {})


def test_a_missing_workspace_is_simply_created(tmp_path):
    """First run of a task: nothing to clear, everything to write."""
    cleared = reset_workspace(tmp_path, ["work/H30"], _fixtures("H30"))
    assert cleared == []
    assert (tmp_path / "work" / "H30" / "verify.py").exists()


def test_the_paired_trial_spec_carries_fixtures():
    """TaskSpec silently dropping fixtures is how the defect reached the gate."""
    from agent.skills.gate import TaskSpec

    spec = TaskSpec(key="E01", goal="g", fixtures={"work/E01/x": "1"})
    assert spec.fixtures == {"work/E01/x": "1"}
    assert TaskSpec(key="E02", goal="g").fixtures == {}
