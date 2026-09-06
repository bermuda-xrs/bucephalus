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

from agent.models import stall  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ALPHA_INJECT_STALL", raising=False)
    stall.reset_injection_counter()
    yield
    stall.reset_injection_counter()


def test_injection_cannot_fire_without_the_flag():
    """The whole safety property. Unset means unreachable."""
    assert stall._inject_on() == set()  # noqa: SLF001
    for _ in range(50):
        stall._CALLS["n"] += 1  # noqa: SLF001
        assert stall._CALLS["n"] not in stall._inject_on()  # noqa: SLF001


def test_injection_fires_only_on_the_named_calls(monkeypatch):
    monkeypatch.setenv("ALPHA_INJECT_STALL", "2,4")
    assert stall._inject_on() == {2, 4}  # noqa: SLF001
    fired = [n for n in range(1, 7) if n in stall._inject_on()]  # noqa: SLF001
    assert fired == [2, 4]


def test_garbage_in_the_flag_disables_it_rather_than_firing(monkeypatch):
    """A malformed switch must fail CLOSED. Failing open would inject stalls
    into a real run on a typo."""
    for bad in ("yes", "all", "-1", "1;2", " ", "abc,def"):
        monkeypatch.setenv("ALPHA_INJECT_STALL", bad)
        assert stall._inject_on() == set(), f"{bad!r} should disable injection"


def test_the_flag_is_read_from_the_environment_only():
    """No config key, no CLI default, nothing that could persist into a run.

    Widened from "once in ollama.py" to "once in the whole package" when a
    second adapter arrived. The narrow version would have passed while a new
    adapter read the switch for itself, which is the failure it exists to
    stop: one read site is one way for injection to fire, and the count is
    the entire guarantee.
    """
    sites = {
        path.relative_to(ROOT).as_posix(): text.count(
            'os.environ.get("ALPHA_INJECT_STALL"')
        for path in (ROOT / "src" / "agent").rglob("*.py")
        if (text := path.read_text(encoding="utf-8"))
        and "ALPHA_INJECT_STALL" in text
    }
    assert sum(sites.values()) == 1, (
        f"the switch must be READ in exactly one place; found {sites}")
    assert sites.get("src/agent/models/stall.py") == 1, (
        "the read site must be the shared stall module, so every adapter "
        f"inherits the same guard rather than writing its own: {sites}")


def test_the_call_counter_is_shared_across_adapters():
    """An injected index means "the Nth model call of this run", not "the Nth
    call on whichever adapter". Phase 1 makes the adapter a config edit, so a
    per-adapter counter would make an injected shakedown produce different
    calls before and after that edit - the switch would stop being a way to
    reproduce a fault and become a way to relabel one."""
    from agent.models import ollama, openai_compat
    assert ollama.next_call_index is stall.next_call_index
    assert openai_compat.next_call_index is stall.next_call_index


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


# ---------------------------------------------------------------------------
# The abort rule: EXCLUDED counts, machine_fault does not.
# ---------------------------------------------------------------------------

def test_a_recovered_machine_fault_carries_no_abort_authority():
    """H23 stalled once, re-ran, and produced a valid measured result. M11
    stalled twice and its data was lost. Only the second is missing data.

    The first version of the rule counted `machine_fault OR excluded`, which
    would have aborted a healthy run on evidence that recovery WORKED.
    """
    n = _noise()
    recovered = {"key": "H23", "status": "plateau", "passed": False,
                 "machine_fault": True, "excluded": False, "unmeasured": False}
    lost = {"key": "M11", "status": "excluded_machine_fault", "passed": False,
            "machine_fault": True, "excluded": True, "unmeasured": True}

    n.assert_machine_fault_never_scored_as_failure(recovered)
    n.assert_machine_fault_never_scored_as_failure(lost)

    assert not recovered["excluded"]
    assert lost["excluded"]


def test_the_abort_threshold_counts_excluded_only():
    n = _noise()
    src = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    assert n.ABORT_EXCLUDED_MAX == 3
    i = src.index("excluded_so_far = sum(")
    j = src.index("ABORT_EXCLUDED_MAX:", i)
    window = src[i:j]
    assert 'x.get("excluded")' in window
    assert "machine_fault" not in window, (
        "machine_fault must not enter the abort count - a recovered stall is "
        "evidence the handler worked, not a reason to stop")


def test_the_bench_catches_the_same_class_the_new_adapter_raises():
    """bench/noise.py catches ModelStall imported from agent.models.ollama.

    Phase 1 added a second adapter. If the OpenAI-compatible one raised a
    DIFFERENT ModelStall - its own class, or one from a module that got
    re-imported under another name - the bench would miss it and score a
    machine event as a task failure, which is the exact defect the whole
    machine-fault path exists to prevent. It is an identity question, so it
    is asked as one rather than inferred from the imports.
    """
    from agent.models import openai_compat

    n = _noise()
    assert n.ModelStall is stall.ModelStall
    assert openai_compat.ModelStall is stall.ModelStall

    from agent.execute import loop as loop_mod
    assert loop_mod.ModelStall is stall.ModelStall


def test_one_stall_re_runs_the_task_and_the_result_still_counts():
    """The re-run, exercised rather than grepped for.

    The existing checks read noise.py for the word `continue` in the stall
    branch. That is a test of spelling: it passes for any code containing the
    token, and it says nothing about whether a second attempt actually happens
    or what the recovered row looks like. Phase 1 made the acceptance question
    "does the machine-fault path work on the NEW adapter", and answering that
    by reasoning about which class gets caught is exactly the inference this
    project keeps getting wrong. So the condition is injected into the retry
    itself and the resulting row is inspected.
    """
    n = _noise()
    calls = {"n": 0}

    def flaky(ctx, spec, run_ix):
        calls["n"] += 1
        if calls["n"] == 1:
            raise stall.ModelStall("first attempt stalled", 0.4, 5.9, 30, 120.0)
        return {"run": run_ix, "task_id": "E01", "status": "done",
                "passed": True, "unmeasured": False, "excluded": False}

    n.run_once = flaky
    out = n.run_with_backend_retry(None, {}, 0, "E01")

    assert calls["n"] == 2, "the first stall must produce exactly one re-run"
    assert out["passed"] is True, "a recovered task is a real result"
    assert out["machine_fault"] is True, "the fault is recorded, not hidden"
    assert out["excluded"] is False and out["unmeasured"] is False, (
        "recovery means the data exists - excluding it would discard a "
        "measured result on evidence that the handler WORKED")
    assert out["stalls"][0]["observed_tok_s"] == 0.4
    n.assert_machine_fault_never_scored_as_failure(out)


def test_two_stalls_exclude_the_task_and_never_score_it_as_a_failure():
    n = _noise()
    calls = {"n": 0}

    def always(ctx, spec, run_ix):
        calls["n"] += 1
        raise stall.ModelStall("stalled again", 0.2, 5.9, 12, 90.0)

    n.run_once = always
    out = n.run_with_backend_retry(None, {}, 0, "M11")

    assert calls["n"] == 2, "exactly two attempts, then stop"
    assert out["status"] == "excluded_machine_fault"
    assert out["excluded"] is True and out["unmeasured"] is True
    assert out["passed"] is False
    assert len(out["stalls"]) == 2
    # The invariant that both aborted Phase 2A attempts violated.
    n.assert_machine_fault_never_scored_as_failure(out)
