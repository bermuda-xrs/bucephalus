from __future__ import annotations

import json

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
    stale = tmp_path / "work" / "E01" / "output" / "answer.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"v_circular_m_s": 7672.6}), encoding="utf-8")
    assert stale.exists()

    reset_workspace(tmp_path, ["work/E01"], _fixtures())

    assert not stale.exists(), "the previous run's answer survived the reset"
    assert (tmp_path / "work" / "E01" / "input" / "params.json").exists()


def test_both_arms_of_a_pair_start_from_identical_bytes(tmp_path):
    scope, fx = ["work/E01"], _fixtures()

    reset_workspace(tmp_path, scope, fx)
    ans = tmp_path / "work" / "E01" / "output" / "answer.json"
    ans.write_text(json.dumps({"v_circular_m_s": 7672.6}), encoding="utf-8")

    reset_workspace(tmp_path, scope, fx)
    assert not ans.exists(), \
        "the control arm would have been graded against the skill arm's answer"


def test_an_edited_fixture_is_restored_not_merged(tmp_path):
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
    for scope in (["src"], ["tests"], ["anchor"], ["skills_store"], ["bench/tasks"]):
        with pytest.raises(UnsafeScope):
            reset_workspace(tmp_path, scope, {})


def test_it_refuses_a_scope_outside_the_project(tmp_path):
    with pytest.raises(UnsafeScope):
        reset_workspace(tmp_path / "proj", ["../../elsewhere"], {})


def test_a_missing_workspace_is_simply_created(tmp_path):
    cleared = reset_workspace(tmp_path, ["work/H30"], _fixtures("H30"))
    assert cleared == []
    assert (tmp_path / "work" / "H30" / "verify.py").exists()


def test_the_paired_trial_spec_carries_fixtures():
    from agent.skills.gate import TaskSpec

    spec = TaskSpec(key="E01", goal="g", fixtures={"work/E01/x": "1"})
    assert spec.fixtures == {"work/E01/x": "1"}
    assert TaskSpec(key="E02", goal="g").fixtures == {}
