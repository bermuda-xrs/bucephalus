from __future__ import annotations

import json

import pytest
from conftest import make_config

from agent import evals
from agent import families as F
from agent.models.mock import MockBackend


def _raw(tmp_path):
    raw = make_config(tmp_path / "base").model_dump(mode="json")
    for b in raw["backends"].values():
        b.update(trust_tier="local", input_cost_per_mtok=0.0, output_cost_per_mtok=0.0, cache_read_per_mtok=0.0,
                 cache_write_per_mtok=0.0)
    return raw


def _answering(answer_for):
    def factory(cfg):
        def responder(prefix, tail):
            return answer_for(cfg, tail)
        return {n: MockBackend(n, b, responder=responder) for n, b in cfg.backends.items()}
    return factory


def _final(obj):
    return "<final>" + json.dumps(obj) + "</final>"


def test_overrides_reach_the_backend_mechanisms_limits_and_settings():
    raw = {"backends": {"cloud": {"kind": "mock", "model": "m"}}, "mechanisms": {}, "limits": {}}
    got = evals.apply_overrides(raw, {"backend.tool_profile": "small", "mechanisms.stuck_detection": False,
                                      "limits.context_fill_ratio": 0.5, "long_files": "whole"})
    assert got["backends"]["cloud"]["tool_profile"] == "small" and got["mechanisms"]["stuck_detection"] is False
    assert got["limits"]["context_fill_ratio"] == 0.5 and got["long_files"] == "whole" and "tool_profile" not in raw["backends"]["cloud"]
    with pytest.raises(ValueError):
        evals.apply_overrides(raw, {"nonsense.key": 1})


def test_an_instance_runs_in_its_own_root_and_is_graded(tmp_path):
    spec = F.generate("needle", 3)
    want = spec["output_schema"]["x-expected"]
    right = evals.run_instance(_raw(tmp_path), spec, tmp_path / "a",
                               backends_factory=_answering(lambda c, t: _final(want)))
    wrong = evals.run_instance(_raw(tmp_path), spec, tmp_path / "b",
                               backends_factory=_answering(lambda c, t: _final({"shelf_mark": "nope"})))
    assert right["passed"] and not wrong["passed"] and right["key"] == spec["key"] and right["family"] == "needle"
    assert (tmp_path / "a" / "work" / "catalogue.md").read_text(encoding="utf-8") == spec["fixtures"]["work/catalogue.md"]


def test_a_program_family_is_graded_by_its_own_grader(tmp_path):
    spec = F.generate("write_function", 4)
    (path, source), = spec["reference"].items()
    act = '<action tool="write_file">' + json.dumps({"path": path, "content": source}) + "</action>"
    steps = iter([act, _final({"path": path})])
    raw = _raw(tmp_path)
    raw["autonomy"]["approve_scoped_writes"] = True
    got = evals.run_instance(raw, spec, tmp_path / "w", backends_factory=_answering(lambda c, t: next(steps)))
    assert got["passed"], got


def test_the_measurement_settings_are_forced():
    raw = evals.measurement_raw({"backends": {"cloud": {"kind": "mock", "model": "m"}}}, model="x:1b")
    assert raw["grader_held_out"] is True and raw["isolate_retrieval"] is True
    assert raw["backends"]["cloud"]["model"] == "x:1b" and raw["autonomy"]["auto_review"] is False


def test_paired_summary_counts_and_tests():
    rows = {"base": [{"key": f"k{i}", "family": "f", "passed": i < 2} for i in range(6)],
            "arm": [{"key": f"k{i}", "family": "f", "passed": i < 5} for i in range(6)]}
    s = evals.summarise(rows, "base")
    assert s["arms"]["arm"]["passes"] == 5 and s["arms"]["base"]["passes"] == 2 and s["n"] == 6
    v = s["vs_base"]["arm"]
    assert (v["only_base"], v["only_arm"], v["both"], v["neither"]) == (0, 3, 2, 1)
    assert v["mcnemar_exact_p"] == pytest.approx(0.25) and s["families"]["f"]["arm"] == 5


def test_tune_picks_the_flag_that_helps_on_dev_and_confirms_it_on_heldout(tmp_path):
    def answer(cfg, tail):
        if cfg.backends["cloud"].read_all_pages:
            spec = next(s for s in F.instances(["needle"]) if s["goal"].split("[")[0].strip() in tail)
            return _final(spec["output_schema"]["x-expected"])
        return _final({"shelf_mark": "nope"})

    out = evals.tune(_raw(tmp_path), {"read_all_pages": {"backend.read_all_pages": [False, True]}},
                     F.instances(["needle"], "dev", 2), F.instances(["needle"], "heldout", 2), tmp_path / "t",
                     backends_factory=_answering(answer))
    assert out["winner"] == {"backend.read_all_pages": True} and out["design"] == "factorial"
    assert out["heldout"]["arms"]["winner"]["passes"] == 2 and out["heldout"]["arms"]["base"]["passes"] == 0
    assert "card" in out and out["profile"]["backends"]["cloud"]["read_all_pages"] is True


def test_too_many_combinations_fall_back_to_one_factor_at_a_time():
    flags = {f"f{i}": {f"backend.x{i}": [False, True]} for i in range(6)}
    configs, design = evals.design(flags, max_configs=16)
    assert design == "one_at_a_time" and len(configs) == 7 and configs[0] == {}
    configs, design = evals.design({k: flags[k] for k in ("f0", "f1")}, max_configs=16)
    assert design == "factorial" and len(configs) == 4
