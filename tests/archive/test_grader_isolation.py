"""No expected value may sit anywhere the agent can read.

`work/E01/verify.py` used to hold `EXPECTED = {'v_circular_m_s': 7672.6}` in
plaintext, and `artifact_scope` was `["work/E01"]`. The answer key was inside
the agent's own workspace.

Nothing exploited it because a 3.4B model is not clever enough to try. That is
not a safeguard. M11 generates skills, and a generated skill whose procedure
was "read verify.py and echo the expected value" would score 100% on every
task and present as the largest lift ever measured. The gate's anti-gaming
veto is the guard against exactly that, and it has never fired
(`record_holdout_gap` has no callers), so nothing else was watching.

The grader now lives in `contract.grader_dir` - `grading/<key>` - outside
every path in `artifact_scope`. Only its TEXT is carried into the sandbox,
never as a file the task could open.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def _suite():
    spec = importlib.util.spec_from_file_location(
        "aero_grader_iso", ROOT / "bench" / "suites" / "archive" / "aero_calc.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"-?\d+\.\d+|-?\d+", text))


def test_no_expected_value_is_reachable_from_inside_artifact_scope():
    """The core assertion. Every task, every fixture, every expected value."""
    leaks = []
    for st in _suite().as_suite_tasks():
        scopes = [s.rstrip("/") for s in st.artifact_scope]
        wanted = set()
        for v in st.output_schema.get("required", []):
            pass
        # the expected values themselves, from the grader source
        grader = next((c for p, c in st.fixtures.items() if p.endswith("verify.py")), "")
        m = re.search(r"EXPECTED = (\{.*?\})\n", grader)
        assert m, f"{st.task_key}: no EXPECTED found in its grader"
        expected_nums = _numbers(m.group(1))

        for path, content in st.fixtures.items():
            inside = any(path == s or path.startswith(s + "/") for s in scopes)
            if not inside:
                continue
            overlap = expected_nums & _numbers(content)
            # a task's own inputs legitimately contain numbers; only flag the
            # expected ANSWER appearing verbatim
            hard = {n for n in overlap if len(n) > 3 and "." in n}
            if hard:
                leaks.append(f"{st.task_key}: {path} contains {sorted(hard)}")
    assert not leaks, "expected values reachable inside artifact_scope:\n" + "\n".join(leaks)


def test_the_grader_is_not_inside_any_artifact_scope():
    """Structural, independent of what the grader happens to contain."""
    bad = []
    for st in _suite().as_suite_tasks():
        scopes = [s.rstrip("/") for s in st.artifact_scope]
        for path in st.fixtures:
            if not path.endswith("verify.py"):
                continue
            if any(path == s or path.startswith(s + "/") for s in scopes):
                bad.append(f"{st.task_key}: grader at {path} inside {scopes}")
    assert not bad, "\n".join(bad)


def test_every_task_declares_a_grader_dir_outside_its_scope():
    for st in _suite().as_suite_tasks():
        spec = st.to_spec()
        gd = spec.get("grader_dir")
        assert gd, f"{st.task_key}: no grader_dir"
        for s in st.artifact_scope:
            s = s.rstrip("/")
            assert not (gd == s or gd.startswith(s + "/")), \
                f"{st.task_key}: grader_dir {gd} is inside scope {s}"
        assert any(p.startswith(gd + "/") for p in st.fixtures), \
            f"{st.task_key}: grader_dir {gd} has no fixture"


def test_a_grader_left_inside_scope_is_caught(tmp_path):
    """The guard must be able to fail, or it guards nothing."""
    from agent.skills.gate import TaskSpec

    bad = TaskSpec(key="X01", goal="g", artifact_scope=["work/X01"],
                   fixtures={"work/X01/verify.py": "EXPECTED = {'v': 1.0}\n"})
    scopes = [s.rstrip("/") for s in bad.artifact_scope]
    inside = [p for p in bad.fixtures
              if p.endswith("verify.py")
              and any(p == s or p.startswith(s + "/") for s in scopes)]
    assert inside, "the detection logic itself is broken"


def test_the_verifier_reads_the_grader_from_outside_scope(tmp_path, monkeypatch):
    """End to end through the real verifier, with root resolution."""
    from agent.assure.verifiers import VerifyProgramVerifier
    from agent.kernel.types import TaskContract

    (tmp_path / "grading" / "T1").mkdir(parents=True)
    (tmp_path / "work" / "T1" / "output").mkdir(parents=True)
    (tmp_path / "grading" / "T1" / "verify.py").write_text(
        "import json, pathlib\n"
        "def check():\n"
        "    p = pathlib.Path('output/answer.json')\n"
        "    if not p.exists():\n        return False, 0.0\n"
        "    v = json.loads(p.read_text())\n"
        "    return v.get('x') == 42, (1.0 if v.get('x') == 42 else 0.0)\n",
        encoding="utf-8")
    (tmp_path / "work" / "T1" / "output" / "answer.json").write_text(
        json.dumps({"x": 42}), encoding="utf-8")

    contract = TaskContract(goal="t", success_criterion="verify_program",
                            artifact_scope=["work/T1"], grader_dir="grading/T1")
    r = VerifyProgramVerifier().check("", contract, root=str(tmp_path))
    assert r.passed and r.score == 1.0, r.observation
    assert not (tmp_path / "work" / "T1" / "verify.py").exists()


def test_the_verifier_no_longer_depends_on_the_process_directory(tmp_path, monkeypatch):
    """It resolved against CWD, so a harness run from elsewhere scored 0%.

    That failure was silent and looked exactly like a suite that was too hard.
    """
    from agent.assure.verifiers import VerifyProgramVerifier
    from agent.kernel.types import TaskContract

    (tmp_path / "grading" / "T2").mkdir(parents=True)
    (tmp_path / "work" / "T2" / "output").mkdir(parents=True)
    (tmp_path / "grading" / "T2" / "verify.py").write_text(
        "import json, pathlib\n"
        "def check():\n"
        "    v = json.loads(pathlib.Path('output/answer.json').read_text())\n"
        "    return v.get('x') == 1, 1.0\n", encoding="utf-8")
    (tmp_path / "work" / "T2" / "output" / "answer.json").write_text(
        json.dumps({"x": 1}), encoding="utf-8")

    elsewhere = tmp_path / "somewhere_else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)          # deliberately the wrong directory

    contract = TaskContract(goal="t", success_criterion="verify_program",
                            artifact_scope=["work/T2"], grader_dir="grading/T2")
    r = VerifyProgramVerifier().check("", contract, root=str(tmp_path))
    assert r.passed, f"CWD still leaks into resolution: {r.observation}"


def test_a_promotion_states_whether_the_gaming_veto_ran():
    """M7.4 rule 4 is inoperative for verify_program suites. Say so.

    `record_holdout_gap` has no callers, and the only visible/held-out
    partition lives in the M11 generator, which has never run. A promotion
    that silently skipped the check looks identical to one that passed it.
    """
    src = (ROOT / "src" / "agent" / "skills" / "gate.py").read_text(encoding="utf-8")
    assert "gaming_check" in src
    assert "NOT MEASURED" in src, "an unrun veto must be named in the verdict"
