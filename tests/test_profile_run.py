from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))


def _specs():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "bench" / "tasks").glob("*.json"))]


def test_every_grader_a_task_names_is_where_the_run_will_look_for_it(tmp_path):
    import profile_run

    profile_run.prepare_data(tmp_path, str(ROOT / "bench" / "tasks"))
    named = [s["grader_dir"] for s in _specs() if s.get("grader_dir")]
    assert named, "no bench task names a grader: this test has nothing to check"
    for g in named:
        assert (tmp_path / g / "verify.py").is_file(), f"{g}/verify.py is not under the run's root: the verifier can never pass"


def test_a_run_rooted_there_resolves_the_grader_as_the_loop_does(tmp_path):
    import profile_run
    from agent.config import Config

    profile_run.prepare_data(tmp_path, str(ROOT / "bench" / "tasks"))
    cfg = Config(root=str(tmp_path), backends={})
    g = next(s["grader_dir"] for s in _specs() if s.get("grader_dir"))
    assert (cfg.resolve(g) / "verify.py").is_file()


def test_nothing_else_of_the_repository_is_copied(tmp_path):
    import profile_run

    profile_run.prepare_data(tmp_path, str(ROOT / "bench" / "tasks"))
    top = {p.name for p in tmp_path.iterdir()}
    assert top <= {"bench"}, top


def test_a_suite_with_no_graders_copies_nothing(tmp_path):
    import profile_run

    d = tmp_path / "suite"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"key": "a", "goal": "g"}), encoding="utf-8")
    out = tmp_path / "data"
    out.mkdir()
    profile_run.prepare_data(out, str(d))
    assert list(out.iterdir()) == []
