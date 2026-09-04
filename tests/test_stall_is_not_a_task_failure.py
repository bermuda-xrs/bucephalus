"""A stalled model call must not be scored as a task failure.

The detector was added, fired correctly, and was thrown away. `run_task`
catches every exception, records `task.crashed` and returns a status the
suite scores as FAILED - so `ModelStall` never reached the handler written to
re-run the task. Two tasks in a Phase 2A run were mis-scored that way before
the run was aborted:

    H23  ModelStall: 0.59 tok/s against a median of 5.97  -> "crashed"
    H30  transport timeout after ~1728 s                  -> "crashed"

The detector was correct at both ends and useless in the middle, because it
was caught one level too low. That is why these tests assert the PROPAGATION
and not the detection: a test that ModelStall is raised would have passed
throughout the whole defect.

Why it matters beyond bookkeeping: the budget is wall clock and
`budget_exhausted` is a failure, so a machine stall silently converts a task
that would have passed into one that failed. In a paired trial that
manufactures a discordant pair out of a machine event, and interleaving the
arms cannot help, because one stalled call lands on exactly one arm.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.execute import loop as loop_mod  # noqa: E402
from agent.models.ollama import ModelStall  # noqa: E402


def test_the_handler_sits_ahead_of_the_generic_catch():
    """Order is the entire defect. Behind it, the handler never runs."""
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(encoding="utf-8")
    stall = src.index("    except ModelStall as e:")
    generic = src.index("    except Exception as e:", src.index("def run_task"))
    assert stall < generic, (
        "the ModelStall handler must precede `except Exception`, or the "
        "generic branch swallows it into `crashed` and the suite scores a "
        "machine event as a task failure")


def test_a_timeout_is_raised_as_a_stall_not_a_generic_backend_error():
    """A transport timeout is the same machine event, caught by a different
    layer. If it arrives as a plain BackendError it lands in the generic
    branch and is scored as a failure - which is what happened to H30."""
    src = (ROOT / "src" / "agent" / "models" / "ollama.py").read_text(encoding="utf-8")
    assert "except httpx.TimeoutException" in src
    i = src.index("except httpx.TimeoutException")
    j = src.index("except Exception", i)
    assert "raise ModelStall" in src[i:j], (
        "a transport timeout must be raised as ModelStall, or it is "
        "indistinguishable from a real backend failure")


def test_model_stall_propagates_out_of_run_task(monkeypatch):
    """The regression, stated as the property that was actually broken."""
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise ModelStall("stalled", 0.2, 2.5, 30, 150.0)

    # _step is where a model call happens; any raise from inside the loop body
    # must reach the caller rather than becoming `crashed`.
    assert hasattr(loop_mod, "run_task")
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(encoding="utf-8")

    # The sweep must be attempted BEFORE re-raising: a stall can land on a
    # call made after the answer was already written, and re-running would
    # discard a correct artifact. Because generation is not byte-deterministic
    # here, discarding a verified artifact can turn a pass into a fail, which
    # makes it a correctness problem rather than wasted compute.
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
    """Excluded is reported as excluded: never a failure, never dropped."""
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
    """`unmeasured` is what keeps it out of the pass rate."""
    src = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    i = src.index("excluded_machine_fault")
    branch = src[i - 200:i + 800]
    assert '"unmeasured": True' in branch, (
        "an excluded task must be unmeasured, or a machine fault still "
        "depresses the pass rate")


# ---------------------------------------------------------------------------
# Path (b): a stall that lands AFTER the work is already done.
#
# Proved deterministically with the mock backend rather than on real hardware.
# Two attempts to provoke it with an injected stall on a live model failed for
# an unrelated reason - `run_code` errored on the first call, so there was no
# artifact for the sweep to find - and chasing a specific branch through model
# non-determinism is the wrong instrument. The sweep is the branch under test,
# so the test controls what is on disk.
#
# Why the branch matters: re-running a task whose answer is already correct
# can turn a pass into a fail, because generation here is NOT
# byte-deterministic (SPEC-DELTA C18). Discarding a verified artifact is a
# correctness problem, not wasted compute.
# ---------------------------------------------------------------------------

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
    """The correctness case: sweep first, do not discard a correct artifact."""
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
            return write            # the work gets done
        raise ModelStall("stall AFTER the artifact was written", 0.1, 2.5, 5, 60.0)

    for name in ("cloud", "local", "review"):
        ctx.backends[name]._responder = responder  # noqa: SLF001

    t = create_task(ctx.conn, ctx.log, TaskContract(
        goal=spec.goal, success_criterion="verify_program",
        artifact_scope=spec.artifact_scope, grader_dir=spec.grader_dir),
        Budget(usd=1.0, seconds=600))

    # It must NOT raise: the work is complete, so there is nothing to re-run.
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
    """The other half: nothing on disk, so the caller must get the stall."""
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
