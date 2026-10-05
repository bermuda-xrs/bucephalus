from __future__ import annotations

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
    for bad in ("yes", "all", "-1", "1;2", " ", "abc,def"):
        monkeypatch.setenv("ALPHA_INJECT_STALL", bad)
        assert stall._inject_on() == set(), f"{bad!r} should disable injection"


def test_the_flag_is_read_from_the_environment_only():
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
    from agent.models import ollama, openai_compat
    assert ollama.next_call_index is stall.next_call_index
    assert openai_compat.next_call_index is stall.next_call_index


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
    n = _noise()
    for row in (
        {"key": "H23", "status": "crashed", "passed": False,
         "stalls": [{"observed_tok_s": 0.59, "median_tok_s": 5.97}]},
        {"key": "H30", "status": "crashed", "passed": False,
         "error": "ollama call failed at http://localhost:11435: timed out"},
        {"key": "E06", "status": "crashed", "passed": False},
    ):
        with pytest.raises(AssertionError, match="a path was missed"):
            n.assert_machine_fault_never_scored_as_failure(row)


def test_a_normal_failure_is_untouched():
    n = _noise()
    n.assert_machine_fault_never_scored_as_failure(
        {"key": "E01", "status": "plateau", "passed": False})
    n.assert_machine_fault_never_scored_as_failure(
        {"key": "E02", "status": "max_steps", "passed": False})


def test_a_recovered_task_may_carry_machine_fault_and_still_be_measured():
    n = _noise()
    n.assert_machine_fault_never_scored_as_failure(
        {"key": "E03", "status": "done", "passed": True,
         "machine_fault": True, "excluded": False, "unmeasured": False})


def test_the_invariant_runs_on_every_task_not_just_at_the_end():
    src = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    i = src.index("out = run_with_backend_retry(")
    j = src.index("append_run(ledger", i)
    assert "assert_machine_fault_never_scored_as_failure(out)" in src[i:j], (
        "the invariant must be checked between running a task and writing it "
        "to the ledger")


def test_a_recovered_machine_fault_carries_no_abort_authority():
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
    from agent.models import openai_compat

    n = _noise()
    assert n.ModelStall is stall.ModelStall
    assert openai_compat.ModelStall is stall.ModelStall

    from agent.execute import loop as loop_mod
    assert loop_mod.ModelStall is stall.ModelStall


def test_one_stall_re_runs_the_task_and_the_result_still_counts():
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
    n.assert_machine_fault_never_scored_as_failure(out)
