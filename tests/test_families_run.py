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


def _shard(tmp, k, n, rows, planned=None, **over):
    d = tmp / f"s{k}"
    d.mkdir(parents=True)
    keys = planned if planned is not None else sorted({r["key"] for rs in rows.values() for r in rs})
    plan = {"shard": f"{k}/{n}", "split": "dev", "families": ["a"], "per_family": 2, "skip": 0, "budget_seconds": 1800,
            "family_versions": {"a": 1}, "arms": sorted(rows), "keys": {a: keys for a in rows}, **over}
    (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    for arm, rs in rows.items():
        (d / f"{arm}.jsonl").write_text("".join(json.dumps({"family": "a", "status": "done", **r}) + "\n" for r in rs),
                                        encoding="utf-8")
    return d


def test_merging_shards_pools_the_rows_and_summarises_once(tmp_path):
    import families_merge

    dirs = []
    for i, (k, ok) in enumerate((("a-000", True), ("a-002", False))):
        dirs.append(_shard(tmp_path, i, 2, {"base": [{"key": k, "passed": True}], "kept": [{"key": k, "passed": ok}]}))
        (dirs[-1] / "cards.json").write_text(json.dumps({"base": {"machine": f"pc{i}"}}), encoding="utf-8")
    s = families_merge.merge(dirs, tmp_path / "pooled")
    assert s["arms"] == {"base": {"passes": 2, "n": 2}, "kept": {"passes": 1, "n": 2}}
    assert len((tmp_path / "pooled" / "kept.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert [c["base"]["machine"] for c in s["shard_cards"]] == ["pc0", "pc1"]


def test_merging_refuses_one_instance_measured_twice(tmp_path):
    import families_merge
    import pytest

    dirs = [_shard(tmp_path, i, 2, {"base": [{"key": "a-000", "passed": True}]}) for i in range(2)]
    with pytest.raises(ValueError, match="a-000"):
        families_merge.merge(dirs, tmp_path / "pooled")


def test_merging_refuses_a_missing_shard(tmp_path):
    import families_merge
    import pytest

    dirs = [_shard(tmp_path, k, 3, {"base": [{"key": f"a-00{k}", "passed": True}]}) for k in (0, 2)]
    with pytest.raises(ValueError, match="1/3"):
        families_merge.merge(dirs, tmp_path / "pooled")


def test_merging_refuses_shards_split_different_ways(tmp_path):
    import families_merge
    import pytest

    dirs = [_shard(tmp_path, 0, 2, {"base": [{"key": "a-000", "passed": True}]}),
            _shard(tmp_path, 1, 3, {"base": [{"key": "a-001", "passed": True}]})]
    with pytest.raises(ValueError, match="same N"):
        families_merge.merge(dirs, tmp_path / "pooled")


def test_merging_refuses_shards_run_with_different_settings(tmp_path):
    import families_merge
    import pytest

    dirs = [_shard(tmp_path, 0, 2, {"base": [{"key": "a-000", "passed": True}]}),
            _shard(tmp_path, 1, 2, {"base": [{"key": "a-001", "passed": True}]}, budget_seconds=600)]
    with pytest.raises(ValueError, match="budget_seconds"):
        families_merge.merge(dirs, tmp_path / "pooled")


def test_merging_refuses_an_unfinished_shard(tmp_path):
    import families_merge
    import pytest

    dirs = [_shard(tmp_path, 0, 2, {"base": [{"key": "a-000", "passed": True}]}),
            _shard(tmp_path, 1, 2, {"base": [{"key": "a-001", "passed": True}]}, planned=["a-001", "a-003"])]
    with pytest.raises(ValueError, match="a-003"):
        families_merge.merge(dirs, tmp_path / "pooled")
    assert not (tmp_path / "pooled").exists()


def test_merging_refuses_a_folder_without_a_plan(tmp_path):
    import families_merge
    import pytest

    d = _shard(tmp_path, 0, 1, {"base": [{"key": "a-000", "passed": True}]})
    (d / "plan.json").unlink()
    with pytest.raises(ValueError, match="plan.json"):
        families_merge.merge([d], tmp_path / "pooled")


def test_a_run_writes_its_plan_before_it_starts(tmp_path, monkeypatch):
    _, seen, out = _run(tmp_path / "a", monkeypatch, "--shard", "1/2", "--n", "5")
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert plan["shard"] == "1/2" and plan["arms"] == ["base", "kept"]
    assert plan["keys"]["base"] == [s["key"] for s in seen["specs"][::2]] and plan["family_versions"] == {"arithmetic": 1}


def test_a_dry_run_shows_the_share_and_loads_no_model(tmp_path, monkeypatch, capsys):
    rc, seen, out = _run(tmp_path / "a", monkeypatch, "--shard", "0/2", "--n", "5", "--dry-run")
    assert rc == 0 and seen["loads"] == [] and seen["specs"] == []
    assert not (out / "base.jsonl").exists()
    plan, printed = json.loads((out / "plan.json").read_text(encoding="utf-8")), capsys.readouterr().out
    assert len(plan["keys"]["base"]) == 3 and all(k in printed for k in plan["keys"]["base"])


def test_a_dry_run_then_the_real_run_agree(tmp_path, monkeypatch):
    import families_merge

    dirs = []
    for k in range(2):
        _run(tmp_path / f"d{k}", monkeypatch, "--shard", f"{k}/2", "--n", "3", "--dry-run")
        _, _, out = _run(tmp_path / f"r{k}", monkeypatch, "--shard", f"{k}/2", "--n", "3")
        dirs.append(out)
    s = families_merge.merge(dirs, tmp_path / "pooled")
    assert s["arms"]["base"]["n"] == 3 and s["shards"] == 2


def test_resuming_into_a_folder_with_other_settings_is_refused(tmp_path, monkeypatch):
    import families_run

    _, _, out = _run(tmp_path / "a", monkeypatch, "--shard", "0/2", "--dry-run")
    before = (out / "plan.json").read_text(encoding="utf-8")
    rc = families_run._main(["--families", "arithmetic", "--n", "2", "--arms", "base,kept", "--model", "granite4.1:8b",
                             "--out", str(out), "--shard", "0/2", "--budget-seconds", "1800"])
    assert rc == 2 and (out / "plan.json").read_text(encoding="utf-8") == before
    assert not (out / "base.jsonl").exists()
