"""A model-server failure must not be counted as task difficulty.

An ollama ReadTimeout on H27 killed a ten-task pass outright: one slow call,
and every remaining task was lost. In a five-hour calibration that is the
difference between a result and a wasted night.

The fix must not be to swallow it either. Recording a backend error as
`passed=False` would let a network problem masquerade as evidence the model
cannot do the task — the same silent-0% trap as a verifier that cannot find
its script. So a backend error:

  * is retried once,
  * then recorded with its own status and `unmeasured: True`,
  * is EXCLUDED from the pass-rate denominator,
  * and is reported loudly enough that it cannot be mistaken for a result.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load():
    spec = importlib.util.spec_from_file_location("noise_be", ROOT / "bench" / "noise.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


noise = _load()
from agent.models.base import BackendError  # noqa: E402


def _spec(key="H27"):
    return {"key": key, "goal": "g", "artifact_scope": [], "fixtures": {},
            "output_schema": {"x-tier": "hard", "x-split": "eval"}}


def test_a_transient_backend_error_is_retried_once(monkeypatch):
    calls = {"n": 0}

    def flaky(ctx, spec, run_ix):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BackendError("ollama call failed: timed out")
        return {"run": run_ix, "task_id": "t1", "status": "done",
                "passed": True, "wall_s": 1.0, "free_tokens": 0, "cost_usd": 0.0}

    monkeypatch.setattr(noise, "run_once", flaky)
    monkeypatch.setattr(noise.time, "sleep", lambda s: None)
    out = noise.run_with_backend_retry(None, _spec(), 0, "H27")
    assert calls["n"] == 2
    assert out["passed"] is True
    assert not out.get("unmeasured")


def test_a_persistent_backend_error_is_unmeasured_not_failed(monkeypatch):
    """The important one. `passed=False` alone would be a silent lie."""
    def always(ctx, spec, run_ix):
        raise BackendError("ollama call failed: timed out")

    monkeypatch.setattr(noise, "run_once", always)
    monkeypatch.setattr(noise.time, "sleep", lambda s: None)
    out = noise.run_with_backend_retry(None, _spec(), 0, "H27")
    assert out["status"] == "backend_error"
    assert out["unmeasured"] is True
    assert "timed out" in out["error"]


def test_one_slow_call_does_not_abort_the_whole_pass(monkeypatch):
    """The failure that cost ten tasks: BackendError propagated out of main."""
    def always(ctx, spec, run_ix):
        raise BackendError("boom")

    monkeypatch.setattr(noise, "run_once", always)
    monkeypatch.setattr(noise.time, "sleep", lambda s: None)
    # must return a row rather than raise, so the loop continues
    out = noise.run_with_backend_retry(None, _spec(), 0, "H27")
    assert isinstance(out, dict)


def test_unmeasured_runs_are_excluded_from_the_pass_rate():
    """A task the server never answered is not a task the model failed."""
    runs = [{"passed": False, "unmeasured": True, "status": "backend_error"},
            {"passed": True, "status": "done"}]
    measured = [r for r in runs if not r.get("unmeasured")]
    assert len(measured) == 1
    rate = sum(r["passed"] for r in measured) / len(measured)
    assert rate == 1.0, "the unmeasured run must not drag the rate to 50%"


def test_the_classification_has_a_place_for_unmeasured():
    """stable_fail would be wrong: nothing was observed."""
    body = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    assert '"unmeasured"' in body
    assert "unmeasured_keys" in body, "the report must name them for re-running"
