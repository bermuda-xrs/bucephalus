from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.execute import loop as loop_mod  # noqa: E402
from agent.models.ollama import ModelStall  # noqa: E402


def test_the_handler_sits_ahead_of_the_generic_catch():
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(encoding="utf-8")
    stall = src.index("    except ModelStall as e:")
    generic = src.index("    except Exception as e:", src.index("def run_task"))
    assert stall < generic, (
        "the ModelStall handler must precede `except Exception`, or the "
        "generic branch swallows it into `crashed` and the suite scores a "
        "machine event as a task failure")


@pytest.mark.parametrize("kind,extra", [
    ("ollama", {"endpoint": "http://localhost:11435"}),
    ("openai", {"base_url": "http://localhost:11435/v1"}),
])
def test_a_timeout_is_raised_as_a_stall_not_a_generic_backend_error(
        monkeypatch, kind, extra):
    from agent.config import BackendConfig
    from agent.models import stall as stall_mod
    from agent.models.registry import build_backend

    monkeypatch.setenv("ALPHA_INJECT_STALL", "1t")
    stall_mod.reset_injection_counter()
    cfg = BackendConfig(kind=kind, model="granite4.1:3b", trust_tier="local",
                        **extra)
    backend = build_backend("t", cfg)

    with pytest.raises(ModelStall) as caught:
        backend.complete("system", "user", None, 32, 0.0)
    assert "timed out" in str(caught.value), (
        "the converted timeout must still say it was a timeout, or the "
        "machine-fault record loses the cause")
    stall_mod.reset_injection_counter()


def test_model_stall_propagates_out_of_run_task(monkeypatch):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise ModelStall("stalled", 0.2, 2.5, 30, 150.0)

    assert hasattr(loop_mod, "run_task")
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(encoding="utf-8")

    stall_branch = src[src.index("    except ModelStall as e:"):
                       src.index("    except Exception as e:",
                                 src.index("def run_task"))]
    assert "_final_sweep" in stall_branch, (
        "a stall must sweep the artifact before giving up on the task")
    assert "raise" in stall_branch, (
        "an unfinished stalled task must re-raise so the caller can re-run it")
    assert 'done_unannounced' in stall_branch, (
        "a stall that landed after the work was complete must return the "
        "swept pass, not be re-run")


def test_noise_treats_a_stall_as_machine_fault_and_excludes_on_the_second():
    src = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    assert "except ModelStall as e:" in src
    assert "excluded_machine_fault" in src
    i = src.index("except ModelStall as e:")
    branch = src[i:i + 2000]
    assert "continue" in branch, "the first stall must re-run the task once"
    assert '"excluded": True' in branch, (
        "a second stall must mark the task excluded, not failed")
    assert '"passed": False' in branch


def test_an_excluded_task_is_not_counted_as_a_failure():
    src = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    i = src.index("excluded_machine_fault")
    branch = src[i - 200:i + 800]
    assert '"unmeasured": True' in branch, (
        "an excluded task must be unmeasured, or a machine fault still "
        "depresses the pass rate")


VERIFY_B = """\
import json, pathlib


def check():
    p = pathlib.Path("output/answer.json")
    if not p.exists():
        return False, 0.0
    return json.loads(p.read_text(encoding="utf-8")).get("x") == 42, 1.0
"""


def test_a_stall_after_the_answer_is_written_returns_the_swept_pass(
        ctx, workspace, tmp_path, monkeypatch):
    import json as _json

    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract
    from agent.skills.gate import TaskSpec
    from agent.workspace import reset_workspace

    monkeypatch.chdir(tmp_path)
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.limits.max_steps = 4

    key = "SB"
    spec = TaskSpec(
        key=key, goal=f"write work/{key}/output/answer.json with x=42",
        success_criterion="verify_program", artifact_scope=[f"work/{key}"],
        grader_dir=f"grading/{key}",
        fixtures={f"grading/{key}/verify.py": VERIFY_B,
                  f"work/{key}/output/.keep": ""},
    )
    reset_workspace(ctx.config.root, spec.artifact_scope, spec.fixtures)

    write = _json.dumps({"kind": "code", "code": (
        "import json,pathlib\n"
        f"p=pathlib.Path('work/{key}/output/answer.json')\n"
        "p.parent.mkdir(parents=True,exist_ok=True)\n"
        "p.write_text(json.dumps({'x':42}))\n")})

    calls = {"n": 0}

    def responder(prefix, tail):
        calls["n"] += 1
        if calls["n"] == 1:
            return write
        raise ModelStall("stall AFTER the artifact was written", 0.1, 2.5, 5, 60.0)

    for name in ("cloud", "local", "review"):
        ctx.backends[name]._responder = responder  # noqa: SLF001

    t = create_task(ctx.conn, ctx.log, TaskContract(
        goal=spec.goal, success_criterion="verify_program",
        artifact_scope=spec.artifact_scope, grader_dir=spec.grader_dir),
        Budget(usd=1.0, seconds=600))

    res = run_task(ctx, t.id)
    assert res.status == "done_unannounced", (
        f"a stall landing after the answer was written must return the swept "
        f"pass, not discard it and re-run. got {res.status!r}")

    kinds = [r[0] for r in ctx.conn.execute(
        "SELECT type FROM events WHERE task_id=?", (t.id,))]
    assert "task.model_stalled" in kinds, "the stall must still be recorded"
    assert "verify.unannounced_pass" in kinds, "the sweep must leave a record"


def test_a_stall_before_any_answer_re_raises_for_the_caller(
        ctx, workspace, tmp_path, monkeypatch):
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract
    from agent.skills.gate import TaskSpec
    from agent.workspace import reset_workspace

    monkeypatch.chdir(tmp_path)
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.limits.max_steps = 4

    key = "SC"
    spec = TaskSpec(
        key=key, goal=f"write work/{key}/output/answer.json with x=42",
        success_criterion="verify_program", artifact_scope=[f"work/{key}"],
        grader_dir=f"grading/{key}",
        fixtures={f"grading/{key}/verify.py": VERIFY_B,
                  f"work/{key}/output/.keep": ""},
    )
    reset_workspace(ctx.config.root, spec.artifact_scope, spec.fixtures)

    def responder(prefix, tail):
        raise ModelStall("stall before any work", 0.1, 2.5, 3, 60.0)

    for name in ("cloud", "local", "review"):
        ctx.backends[name]._responder = responder  # noqa: SLF001

    t = create_task(ctx.conn, ctx.log, TaskContract(
        goal=spec.goal, success_criterion="verify_program",
        artifact_scope=spec.artifact_scope, grader_dir=spec.grader_dir),
        Budget(usd=1.0, seconds=600))

    with pytest.raises(ModelStall):
        run_task(ctx, t.id)
