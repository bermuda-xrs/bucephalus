from __future__ import annotations

import json
import sys

import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))


def _specs():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "bench" / "tasks").glob("*.json"))]


def test_every_grader_a_task_names_is_where_the_run_will_look_for_it(tmp_path):
    import profile_run

    profile_run.prepare_data(tmp_path, str(ROOT / "bench" / "tasks"))
    named = [s["grader_dir"] for s in _specs() if s.get("grader_dir")]
    assert named, "no bench task names a grader: this test has nothing to check"
    for g in named:
        assert (tmp_path / g / "verify.py").is_file(), f"{g}/verify.py is not under the run's root: the verifier can never pass"


def test_a_run_rooted_there_resolves_the_grader_as_the_loop_does(tmp_path):
    import profile_run
    from agent.config import Config

    profile_run.prepare_data(tmp_path, str(ROOT / "bench" / "tasks"))
    cfg = Config(root=str(tmp_path), backends={})
    g = next(s["grader_dir"] for s in _specs() if s.get("grader_dir"))
    assert (cfg.resolve(g) / "verify.py").is_file()


def test_nothing_else_of_the_repository_is_copied(tmp_path):
    import profile_run

    profile_run.prepare_data(tmp_path, str(ROOT / "bench" / "tasks"))
    top = {p.name for p in tmp_path.iterdir()}
    assert top <= {"bench"}, top


def test_a_suite_with_no_graders_copies_nothing(tmp_path):
    import profile_run

    d = tmp_path / "suite"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"key": "a", "goal": "g"}), encoding="utf-8")
    out = tmp_path / "data"
    out.mkdir()
    profile_run.prepare_data(out, str(d))
    assert list(out.iterdir()) == []


def test_the_8b_arms_are_declared_with_the_requested_context_and_profiles(tmp_path):
    import profile_run
    import yaml

    for arm, profile in (("full8b", "full"), ("small8b", "small")):
        raw = yaml.safe_load(profile_run.write_config(ROOT / "config.local.yaml", arm, tmp_path).read_text(encoding="utf-8"))
        b = raw["backends"]["cloud"]
        assert b["model"] == "granite4.1:8b" and b["usable_context_window"] == 8192 and b["tool_profile"] == profile
    ctl = yaml.safe_load(profile_run.write_config(ROOT / "config.local.yaml", "control", tmp_path).read_text(encoding="utf-8"))
    assert ctl["backends"]["cloud"]["model"] == "granite4.1:3b"


def test_the_preflight_says_no_when_memory_is_short_and_yes_when_it_is_not():
    import profile_run

    w = 5.1
    need = profile_run.needed_gb(w, 8192)
    assert 7.0 < need < 8.0
    assert not profile_run.fits(w, 8192, 4.0) and not profile_run.fits(w, 8192, need)
    assert profile_run.fits(w, 8192, need + profile_run.MARGIN_GB + 0.01)


def test_the_preflight_reads_the_installed_size_and_reports_a_missing_model(tmp_path):
    import profile_run

    models = tmp_path / "m"
    d = models / "manifests" / "registry.ollama.ai" / "library" / "granite4.1"
    d.mkdir(parents=True)
    (d / "8b").write_text(json.dumps({"layers": [{"mediaType": "application/vnd.ollama.image.model", "size": 5 * 2 ** 30},
                                                  {"mediaType": "application/vnd.ollama.image.template", "size": 10}]}),
                          encoding="utf-8")
    got = profile_run.preflight(["control", "small8b"], str(models), available_gb=20.0)
    assert list(got) == ["small8b"] and got["small8b"]["weights_gb"] == 5.0 and got["small8b"]["fits"] is True
    assert profile_run.preflight(["full8b"], str(tmp_path / "none"), available_gb=20.0)["full8b"]["fits"] is False


def test_the_3b_only_run_has_no_preflight_and_unknown_arms_are_refused(capsys):
    import profile_run

    assert profile_run.preflight(["control", "small"], "x", available_gb=1.0) == {}
    with pytest.raises(SystemExit):
        profile_run._main(["--suite", "bench/tasks", "--arms", "control,nonsense"])


def test_a_summary_compares_every_arm_with_the_control(tmp_path):
    import profile_run

    def arm_file(arm, passed):
        results = [{"key": f"t{i}", "passed": p, "status": "done" if p else "plateau", "wall_s": 10.0 + i} for i, p in enumerate(passed)]
        (tmp_path / f"{arm}-0.json").write_text(json.dumps({"arms": {"skills-off": {"results": results}}}), encoding="utf-8")

    arm_file("control", [True, False, False, False])
    arm_file("small8b", [True, True, True, False])
    arm_file("full8b", [False, False, False, False])
    s = profile_run.summarise(tmp_path, 1, ("control", "full8b", "small8b"))
    assert s["n_pairs"] == 4 and s["arms"]["control"]["passes"] == 1 and s["arms"]["small8b"]["passes"] == 3
    assert s["vs_control"]["small8b"] == {"only_control": 0, "only_arm": 2, "both": 1, "neither": 1, "mcnemar_exact_p": 0.5}
    assert s["vs_control"]["full8b"]["only_control"] == 1 and "control" not in s["vs_control"]
    assert s["arms"]["control"]["crashed"] == 0
    assert s["code"]["harness_version"] >= 10


def _fake_run(tmp_path, monkeypatch, arms="control,full8b,small8b", reps=2, load_errors=()):
    import types

    import httpx
    import profile_run
    import yaml
    from agent.models import local

    seen = {"events": [], "resident": {}, "clock": 0.0, "errors": list(load_errors)}

    def run(endpoint, model, root, num_ctx=None):
        seen["clock"] += 100.0
        if seen["errors"]:
            seen["errors"].pop(0)
            raise httpx.HTTPStatusError("500", request=httpx.Request("POST", endpoint),
                                        response=httpx.Response(500))
        seen["resident"][model] = num_ctx
        seen["events"].append(("load", model, num_ctx))
        return {"model": model, "loaded": True, "context_length": num_ctx}

    def unload(endpoint, model):
        seen["resident"].pop(model, None)
        seen["events"].append(("unload", model))
        return {"model": model, "loaded": False}

    def run_arm(cfg, arm, rep, suite, out, models, timeout_s):
        b = yaml.safe_load(Path(cfg).read_text(encoding="utf-8"))["backends"]["cloud"]
        seen["clock"] += 60.0
        seen["events"].append(("arm", arm, b["model"], b.get("usable_context_window") or b["context_window"],
                               dict(seen["resident"])))
        return Path(out) / f"{arm}-{rep}.json"

    monkeypatch.setattr(local, "start_server", lambda *a, **k: None)
    monkeypatch.setattr(local, "stop_owned", lambda: None)
    monkeypatch.setattr(local, "run", run)
    monkeypatch.setattr(local, "unload", unload)
    monkeypatch.setattr(profile_run, "run_arm", run_arm)
    monkeypatch.setattr(profile_run, "preflight", lambda arms, models: {})
    monkeypatch.setattr(profile_run, "time", types.SimpleNamespace(time=lambda: seen["clock"]))
    monkeypatch.setenv("BUCEPHALUS_OLLAMA_MODELS", str(tmp_path))
    rc = profile_run._main(["--suite", "bench/tasks", "--arms", arms, "--reps", str(reps), "--out", str(tmp_path / "out")])
    return rc, seen


def test_every_arm_starts_on_its_own_model_loaded_at_its_own_window_and_nothing_else(tmp_path, monkeypatch):
    rc, seen = _fake_run(tmp_path, monkeypatch)
    arms = [e for e in seen["events"] if e[0] == "arm"]
    assert rc == 0 and len(arms) == 6
    for _, arm, model, window, resident in arms:
        assert resident == {model: window}, (arm, resident)
    assert {(m, w) for _, _, m, w, _ in arms} == {("granite4.1:3b", 16384), ("granite4.1:8b", 8192)}


def test_the_load_is_not_part_of_the_arm_time(tmp_path, monkeypatch, capsys):
    _fake_run(tmp_path, monkeypatch)
    lines = [x for x in capsys.readouterr().out.splitlines() if x.startswith("rep ")]
    assert len(lines) == 6 and all(x.endswith(": 1.0 min") for x in lines), lines


def test_a_load_that_fails_once_is_tried_again(tmp_path, monkeypatch, capsys):
    rc, seen = _fake_run(tmp_path, monkeypatch, load_errors=[1])
    assert rc == 0 and len([e for e in seen["events"] if e[0] == "arm"]) == 6
    assert "trying once more" in capsys.readouterr().out


def test_a_load_that_fails_twice_stops_the_run_before_the_arm(tmp_path, monkeypatch):
    import httpx

    with pytest.raises(httpx.HTTPStatusError):
        _fake_run(tmp_path, monkeypatch, load_errors=[1, 2])
