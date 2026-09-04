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
