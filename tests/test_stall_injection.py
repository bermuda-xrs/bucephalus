"""Fault injection, and the invariant both aborted runs violated.

Stalls occurred about three times in sixty tasks. A short shakedown will
almost certainly see none, and "no stall occurred" is not evidence that the
handler works - it is the same absence of evidence that let the wiring defect
survive two aborted Phase 2A attempts. So the shakedown injects the condition
rather than waiting for it.

The injection itself needs a guard, because a switch that can fire in a real
run is worse than no switch: it would turn a measurement into a fabrication.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from agent.models import ollama as ol  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ALPHA_INJECT_STALL", raising=False)
    ol.reset_injection_counter()
    yield
    ol.reset_injection_counter()


def test_injection_cannot_fire_without_the_flag():
    """The whole safety property. Unset means unreachable."""
    assert ol._inject_on() == set()  # noqa: SLF001
    for _ in range(50):
        ol._CALLS["n"] += 1  # noqa: SLF001
        assert ol._CALLS["n"] not in ol._inject_on()  # noqa: SLF001


def test_injection_fires_only_on_the_named_calls(monkeypatch):
    monkeypatch.setenv("ALPHA_INJECT_STALL", "2,4")
    assert ol._inject_on() == {2, 4}  # noqa: SLF001
    fired = [n for n in range(1, 7) if n in ol._inject_on()]  # noqa: SLF001
    assert fired == [2, 4]


def test_garbage_in_the_flag_disables_it_rather_than_firing(monkeypatch):
    """A malformed switch must fail CLOSED. Failing open would inject stalls
    into a real run on a typo."""
    for bad in ("yes", "all", "-1", "1;2", " ", "abc,def"):
        monkeypatch.setenv("ALPHA_INJECT_STALL", bad)
        assert ol._inject_on() == set(), f"{bad!r} should disable injection"


def test_the_flag_is_read_from_the_environment_only():
    """No config key, no CLI default, nothing that could persist into a run."""
    src = (ROOT / "src" / "agent" / "models" / "ollama.py").read_text(encoding="utf-8")
    assert src.count('os.environ.get("ALPHA_INJECT_STALL"') == 1, (
        "the switch must be READ in exactly one place; more than one read "
        "site is more than one way for it to fire")


# ---------------------------------------------------------------------------
# S4: the invariant both aborted attempts violated.
# ---------------------------------------------------------------------------

def _noise():
    import importlib.util
    spec = importlib.util.spec_from_file_location("noise", ROOT / "bench" / "noise.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_an_excluded_task_must_be_unmeasured():
    n = _noise()
    with pytest.raises(AssertionError, match="unmeasured"):
        n.assert_machine_fault_never_scored_as_failure(
            {"key": "E01", "excluded": True, "unmeasured": False,
             "status": "excluded_machine_fault", "passed": False})


def test_any_crashed_status_is_rejected():
    """H23 and H30 were both recorded as `crashed` and scored FAILED.

    The guard no longer tries to RECOGNISE a machine cause in the row. An
    earlier version looked for a `stalls` key or "timed out" in an error
    string, and a crashed row carrying neither went straight through it -
    which is what an injected transport timeout produced. Recognition is the
    part that kept failing, so every machine event now has its own terminal
    status and `crashed` is simply unreachable for one.
    """
    n = _noise()
    for row in (
        {"key": "H23", "status": "crashed", "passed": False,
         "stalls": [{"observed_tok_s": 0.59, "median_tok_s": 5.97}]},
        {"key": "H30", "status": "crashed", "passed": False,
         "error": "ollama call failed at http://localhost:11435: timed out"},
        # The one the previous guard missed: no stall record, no error text.
        {"key": "E06", "status": "crashed", "passed": False},
    ):
        with pytest.raises(AssertionError, match="a path was missed"):
            n.assert_machine_fault_never_scored_as_failure(row)


def test_a_normal_failure_is_untouched():
    """The invariant must not reject ordinary task failures."""
    n = _noise()
    n.assert_machine_fault_never_scored_as_failure(
        {"key": "E01", "status": "plateau", "passed": False})
    n.assert_machine_fault_never_scored_as_failure(
        {"key": "E02", "status": "max_steps", "passed": False})


def test_a_recovered_task_may_carry_machine_fault_and_still_be_measured():
    """A stall that was re-run and then completed normally is a real result;
    the flag records that a fault occurred, not that the result is void."""
    n = _noise()
    n.assert_machine_fault_never_scored_as_failure(
        {"key": "E03", "status": "done", "passed": True,
         "machine_fault": True, "excluded": False, "unmeasured": False})


def test_the_invariant_runs_on_every_task_not_just_at_the_end():
    """Checked per task so a violation aborts the run rather than being
    discovered in the report three hours later."""
    src = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    i = src.index("out = run_with_backend_retry(")
    j = src.index("append_run(ledger", i)
    assert "assert_machine_fault_never_scored_as_failure(out)" in src[i:j], (
        "the invariant must be checked between running a task and writing it "
        "to the ledger")
