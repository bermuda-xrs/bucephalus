from __future__ import annotations

import inspect as pyinspect
import json
import uuid
from types import SimpleNamespace

import pytest

from agent.assure import verifiers as V
from agent.kernel.types import TaskContract

SENTINEL = f"SENTINEL-{uuid.uuid4().hex}"
ANSWERS = {"empty": "", "braces": "{}",
           "sentinel": json.dumps({"answer": SENTINEL})}

REAL = {"type": "object", "required": ["total"],
        "properties": {"total": {"type": "integer"}},
        "x-expected": {"total": 4825}, "x-contains": ["4825"]}
EMPTY = {"x-expected": {}, "x-contains": [""]}
HOLLOW = {"type": "object", "x-expected": {}, "x-contains": [""]}
CONTRACTS = {"real": REAL, "empty": EMPTY, "hollow": HOLLOW}


def _scripted_ctx():
    return SimpleNamespace(
        backends={"cloud": None}, active_local_tier=lambda: None,
        call_model=lambda *a, **k: SimpleNamespace(text='{"total": 4825}'))


def _registry():
    return V.default_verifiers(_scripted_ctx())


def _kinds():
    return sorted(_registry())


@pytest.fixture
def places(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "work").mkdir(parents=True)
    decoy = tmp_path / "decoy"
    (decoy / "tests").mkdir(parents=True)
    (decoy / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    (decoy / "ok.py").write_text("X = 1\n")
    (decoy / "work").mkdir()
    (decoy / "work" / "verify.py").write_text("def check():\n    return True, 1.0\n")
    monkeypatch.chdir(decoy)
    return root


def test_every_verifier_class_is_registered():
    classes = {obj.kind for _, obj in pyinspect.getmembers(V, pyinspect.isclass)
               if obj.__module__ == V.__name__ and isinstance(getattr(obj, "kind", None), str)
               and callable(getattr(obj, "check", None))}
    assert classes <= set(_registry()), (
        f"verifier classes not in default_verifiers: {sorted(classes - set(_registry()))}")


def test_every_kind_declares_what_it_inspects():
    for name, v in _registry().items():
        assert V.inspects(v) in ("answer", "workspace", "nothing"), name


def test_only_always_pass_inspects_nothing():
    nothing = [n for n, v in _registry().items() if V.inspects(v) == "nothing"]
    assert nothing == ["always_pass"], nothing


@pytest.mark.parametrize("schema_name", sorted(CONTRACTS))
@pytest.mark.parametrize("answer", sorted(ANSWERS))
@pytest.mark.parametrize("kind", _kinds())
def test_every_verifier_can_fail(kind, answer, schema_name, places):
    v = _registry()[kind]
    if V.inspects(v) == "nothing":
        pytest.skip("inspects nothing - held by the mesh under measurement")
    contract = TaskContract(
        goal="What is the invoice total?", success_criterion=kind,
        output_schema=CONTRACTS[schema_name],
        artifact_scope=["work"])
    r = v.check(ANSWERS[answer], contract, root=str(places))
    assert not r.passed, (
        f"{kind} ({V.inspects(v)}) PASSED a {answer!r} answer under the "
        f"{schema_name} contract with an empty workspace - it cannot fail "
        f"there, so its passes mean nothing. observation: {r.observation[:300]}")


def test_under_measurement_a_check_of_nothing_is_not_a_pass(ctx, workspace):
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget

    task = create_task(ctx.conn, ctx.log,
                       TaskContract(goal="g", artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=600))
    assert task.contract.success_criterion == "always_pass"
    assert ctx.mesh.verify(task, "anything").passed
    ctx.config.grader_held_out = True
    assert not ctx.mesh.verify(task, "anything").passed


class _Spy:
    kind = "spy"

    def __init__(self, what):
        self.inspects, self.seen = what, []

    def check(self, artifact, contract, root=None):
        from agent.kernel.types import VerifyResult

        self.seen.append(artifact)
        return VerifyResult(passed=True, score=1.0, observation="ok",
                            verifier_id=self.kind)


def _run_without_an_answer(ctx, scripted, spy):
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget

    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.limits.max_steps = 2
    ctx.mesh.add("spy", spy)
    task = create_task(ctx.conn, ctx.log,
                       TaskContract(goal="g", success_criterion="spy",
                                    artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=600))
    scripted("cloud", *['<action tool="list_dir">{"path": "work"}</action>'] * 4)
    return run_task(ctx, task.id).status


def test_the_sweep_does_not_grade_an_answer_that_was_never_given(ctx, workspace, scripted):
    spy = _Spy("answer")
    status = _run_without_an_answer(ctx, scripted, spy)
    assert spy.seen == [], f"the sweep graded {spy.seen!r} - no answer was given"
    assert status == "max_steps", status


def test_the_sweep_still_grades_the_workspace(ctx, workspace, scripted):
    spy = _Spy("workspace")
    status = _run_without_an_answer(ctx, scripted, spy)
    assert spy.seen, "a workspace verifier was not consulted by the sweep"
    assert status == "done_unannounced", status


from pathlib import Path  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
BENCH_TASKS = sorted((PROJECT / "bench" / "tasks").glob("*.json"))


@pytest.mark.parametrize("path", BENCH_TASKS, ids=lambda p: p.stem)
def test_every_bench_task_grader_can_fail(path, tmp_path, monkeypatch):
    from agent.workspace import reset_workspace

    spec = json.loads(path.read_text(encoding="utf-8"))
    kind = spec.get("success_criterion", "always_pass")
    v = _registry().get(kind)
    assert v is not None and V.inspects(v) != "nothing", (
        f"{spec['key']} is graded by {kind!r}, which grades nothing")
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    reset_workspace(root, spec.get("artifact_scope") or [], spec.get("fixtures") or {})
    grader = spec.get("grader_dir")
    contract = TaskContract(
        goal=spec["goal"], success_criterion=kind,
        output_schema=spec.get("output_schema") or {},
        artifact_scope=spec.get("artifact_scope") or [],
        grader_dir=str(PROJECT / grader) if grader else None)
    for label, answer in ANSWERS.items():
        r = v.check(answer, contract, root=str(root))
        assert not r.passed, (
            f"{spec['key']}: its grader ({kind}) PASSED a {label!r} answer on "
            f"untouched inputs. observation: {r.observation[:300]}")


SLUGIFY_RIGHT = (
    "import re\n\n\ndef slugify(s):\n"
    "    return '-'.join(re.sub(r'[^\\w\\s]', '', s).lower().split())\n")
SLUGIFY_WRONG = "def slugify(s):\n    return s.lower().replace(' ', '-')\n"


@pytest.mark.parametrize("code,want", [(SLUGIFY_RIGHT, True), (SLUGIFY_WRONG, False)])
def test_write_function_002_grader_tells_right_from_wrong(code, want, tmp_path, monkeypatch):
    spec = json.loads((PROJECT / "bench/tasks/002_write_function.json").read_text(encoding="utf-8"))
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "strings.py").write_text(code, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    contract = TaskContract(goal=spec["goal"], success_criterion="verify_program",
                            artifact_scope=["work"],
                            grader_dir=str(PROJECT / spec["grader_dir"]))
    r = V.VerifyProgramVerifier().check('{"path": "work/strings.py"}', contract,
                                        root=str(tmp_path))
    assert r.passed is want, r.observation


def test_ruff_installed_still_fails_an_empty_workspace(places, tmp_path, monkeypatch):
    import os
    import sys

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ruff.py").write_text("import sys\nsys.exit(0)\n")
    (bindir / "ruff.bat").write_text(f'@"{sys.executable}" "%~dp0ruff.py" %*\n')
    (bindir / "ruff").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/ruff.py" "$@"\n')
    os.chmod(bindir / "ruff", 0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    contract = TaskContract(goal="g", success_criterion="ruff", artifact_scope=["work"])
    for answer in ANSWERS.values():
        r = V.RuffVerifier().check(answer, contract, root=str(places))
        assert not r.passed, r.observation


def test_every_registered_verifier_can_pass_a_task_written_for_it():
    from agent.assure.verifiers import default_verifiers

    # `execution` had no script and `citation_check` no sources in the registry: offered everywhere, passable never
    assert not {"execution", "citation_check"} & set(default_verifiers())
