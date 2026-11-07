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


def test_shards_partition_the_instances_and_the_run_takes_only_its_own(tmp_path, monkeypatch):
    keys = []
    for k in range(3):
        _, _, out = _run(tmp_path / f"s{k}", monkeypatch, "--shard", f"{k}/3", "--n", "5")
        keys.append([json.loads(x)["key"] for x in (out / "base.jsonl").read_text(encoding="utf-8").splitlines()])
        assert json.loads((out / "summary.json").read_text(encoding="utf-8"))["shard"] == f"{k}/3"
    _, _, out = _run(tmp_path / "all", monkeypatch, "--n", "5")
    every = [json.loads(x)["key"] for x in (out / "base.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sorted(sum(keys, [])) == sorted(every) and len(set(sum(keys, []))) == len(every)
    assert max(map(len, keys)) - min(map(len, keys)) <= 1


def test_a_bad_shard_is_refused(tmp_path, monkeypatch):
    import pytest

    for bad in ("3/3", "0/0", "x", "-1/2"):
        with pytest.raises(SystemExit):
            _run(tmp_path / bad.replace("/", "_"), monkeypatch, "--shard", bad)


def test_merging_shards_pools_the_rows_and_summarises_once(tmp_path):
    import families_merge

    for i, (k, ok) in enumerate((("a-000", True), ("a-002", False))):
        d = tmp_path / f"s{i}"
        d.mkdir()
        for arm, passed in (("base", True), ("kept", ok)):
            (d / f"{arm}.jsonl").write_text(json.dumps({"key": k, "family": "a", "status": "done", "passed": passed}) + "\n",
                                            encoding="utf-8")
        (d / "cards.json").write_text(json.dumps({"base": {"machine": f"pc{i}"}}), encoding="utf-8")
    s = families_merge.merge([tmp_path / "s0", tmp_path / "s1"], tmp_path / "pooled")
    assert s["arms"] == {"base": {"passes": 2, "n": 2}, "kept": {"passes": 1, "n": 2}}
    assert len((tmp_path / "pooled" / "kept.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert [c["base"]["machine"] for c in s["shard_cards"]] == ["pc0", "pc1"]


def test_merging_refuses_one_instance_measured_twice(tmp_path):
    import families_merge
    import pytest

    for i in range(2):
        d = tmp_path / f"s{i}"
        d.mkdir()
        (d / "base.jsonl").write_text(json.dumps({"key": "a-000", "family": "a", "status": "done", "passed": True}) + "\n",
                                      encoding="utf-8")
    with pytest.raises(ValueError, match="a-000"):
        families_merge.merge([tmp_path / "s0", tmp_path / "s1"], tmp_path / "pooled")
