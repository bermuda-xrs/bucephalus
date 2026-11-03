from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))


def _run(tmp, monkeypatch, *extra):
    import families_run
    from agent import evals
    from agent.models import local

    tmp.mkdir()
    seen = {"loads": [], "specs": []}

    def run_instance(raw, spec, root):
        seen["specs"].append(spec)
        return {"key": spec["key"], "family": spec["family"], "status": "done", "passed": True, "wall_s": 1.0}

    monkeypatch.setattr("agent.runlock.measurement_lock_path", lambda root: tmp / "lock")
    monkeypatch.setattr(local, "start_server", lambda *a, **k: None)
    monkeypatch.setattr(local, "stop_owned", lambda: None)
    monkeypatch.setattr(local, "run", lambda endpoint, model, root, num_ctx=None: seen["loads"].append((model, num_ctx)) or {})
    monkeypatch.setattr(evals, "card_for", lambda raw, root: {})
    monkeypatch.setattr(evals, "run_instance", run_instance)
    rc = families_run._main(["--families", "arithmetic", "--n", "2", "--arms", "base,kept", "--model", "granite4.1:8b",
                             "--out", str(tmp / "out"), *extra])
    return rc, seen, tmp / "out"


def test_the_model_is_warmed_at_the_window_the_agent_will_ask_for(tmp_path, monkeypatch):
    rc, seen, _ = _run(tmp_path / "a", monkeypatch)
    assert rc == 0 and seen["loads"] == [("granite4.1:8b", 16384)]


def test_a_slower_machine_can_be_given_a_longer_budget_and_the_summary_says_so(tmp_path, monkeypatch):
    _, seen, out = _run(tmp_path / "a", monkeypatch)
    assert seen["specs"] and {s["budget_seconds"] for s in seen["specs"]} == {600}
    assert json.loads((out / "summary.json").read_text(encoding="utf-8"))["budget_seconds"] is None
    _, seen, out = _run(tmp_path / "b", monkeypatch, "--budget-seconds", "1800")
    assert seen["specs"] and {s["budget_seconds"] for s in seen["specs"]} == {1800}
    assert json.loads((out / "summary.json").read_text(encoding="utf-8"))["budget_seconds"] == 1800
