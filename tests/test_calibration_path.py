from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time as _t
from pathlib import Path

import pytest

# cmd_paired and noise's main lock beside --config, and these pass a relative
# config.local.yaml: run from the checkout, every test here took the REAL
# .measurement.lock, and refused to start while anything else held it - a
# real measurement, or another xdist worker (an xdist_group did not hold
# under pytest 9.2). From its own tmp_path, each test's lock is its own.
@pytest.fixture(autouse=True)
def _own_measurement_lock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load_bench_run():
    spec = importlib.util.spec_from_file_location(
        "bench_run_cal", ROOT / "bench" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _suite(n_drafting: int, n_eval: int) -> list[dict]:
    out = []
    for i in range(n_drafting):
        out.append({"key": f"D{i:02d}", "goal": f"drafting task {i}",
                    "success_criterion": "verify_program",
                    "output_schema": {"x-split": "drafting", "x-tier": "easy"},
                    "artifact_scope": [f"work/D{i:02d}"]})
    for i in range(n_eval):
        out.append({"key": f"V{i:02d}", "goal": f"eval task {i}",
                    "success_criterion": "verify_program",
                    "output_schema": {"x-split": "eval", "x-tier": "easy"},
                    "artifact_scope": [f"work/V{i:02d}"]})
    return out


class _Ctx:
    def __init__(self, tmp_path, min_paired=15, min_discordant=10):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE skill_trials(skill_id TEXT, task_key TEXT, arm TEXT,"
            " success INT, score REAL, paid_tokens INT, free_tokens INT,"
            " wall_ms INT, created_at REAL)")
        self.conn.execute(
            "CREATE TABLE semantic_cache(key TEXT PRIMARY KEY, value_json TEXT,"
            " taint TEXT, source_hash TEXT, created_at REAL, hits INT)")
        self.conn.execute("CREATE TABLE skills(id TEXT PRIMARY KEY, provenance_json TEXT)")
        gate = type("G", (), {"min_paired_trials": min_paired,
                              "min_discordant_pairs": min_discordant,
                              "min_lift_pp": 5.0, "max_p_value": 0.05})()
        self.config = type("C", (), {
            "gate": gate, "profile": "local", "profile_provenance": {},
            "resolve": staticmethod(lambda x: str(tmp_path / Path(x).name)),
        })()
        self.backends = {"cloud": type("B", (), {
            "cfg": type("BC", (), {"model": "mock-3b"})()})()}
        self.config.autonomy = type("A", (), {"auto_review": True,
                                              "approve_scoped_writes": False})()
        self.closed = False

    def close(self):
        self.closed = True


def _seed(ctx, skill_id, outcomes):
    for key, (c, s) in outcomes.items():
        for arm, val in (("control", c), ("skill", s)):
            ctx.conn.execute(
                "INSERT INTO skill_trials VALUES (?,?,?,?,?,?,?,?,?)",
                (skill_id, key, arm, val, float(val), 0, 100, 1000, _t.time()))
    ctx.conn.commit()


def _declare(ctx, skill_id, prov):
    ctx.conn.execute("INSERT INTO skills VALUES (?,?)", (skill_id, json.dumps(prov)))
    ctx.conn.commit()


def _args(skill_id, split=None, resume=False):
    return type("A", (), {"config": "config.local.yaml", "profile": "local",
                          "suite": "aero_calc", "split": split,
                          "skill_id": skill_id, "out": "calibration.json",
                          "resume": resume, "auto_review": False})()


def _wire(mod, monkeypatch, ctx, suite, on_trial):
    def fake_open(config, *, profile=None, isolate, why=""):
        ctx.config.isolate_retrieval = isolate
        return ctx

    monkeypatch.setattr(mod, "open_measurement", fake_open)
    monkeypatch.setattr(mod, "load_suite", lambda spec, ctx=None: suite)
    monkeypatch.setattr(mod, "run_paired_trial", on_trial)


def test_the_whole_calibration_path_reports_two_numbers(tmp_path, monkeypatch, capsys):
    mod = _load_bench_run()
    suite = _suite(15, 15)
    ctx = _Ctx(tmp_path)

    outcomes = {}
    for i, s in enumerate(suite):
        if i < 12:
            outcomes[s["key"]] = (0, 1)
        elif i == 12:
            outcomes[s["key"]] = (1, 0)
        else:
            outcomes[s["key"]] = (1, 1)

    _declare(ctx, "sk1", {"drafted_by": "a test author",
                          "drafted_from": [s["key"] for s in suite[:15]]})
    _wire(mod, monkeypatch, ctx, suite,
          lambda c, sid, spec, seed=None: _seed(c, sid, {spec.key: outcomes[spec.key]}))
    assert mod.cmd_paired(_args("sk1")) == 0

    out = capsys.readouterr().out
    assert "PRIMARY" in out and "SECONDARY" in out
    assert "UNDERPOWERED" in out

    report = json.loads((tmp_path / "calibration.json").read_text(encoding="utf-8"))
    assert report["primary"]["n"] == 30
    assert report["primary"]["b"] == 12 and report["primary"]["c"] == 1
    assert report["primary"]["verdict"] == "promote"
    assert report["secondary"]["n"] == 15, "eval subset comes from the same trials"
    assert report["profile"] == "local"
    cav = report["primary"]["caveat"]
    assert cav in out, "the contamination caveat must be printed"
    assert "a test author" in cav and "15 of the 30 scored tasks" in cav, cav
    assert "none of the 15 scored tasks" in report["secondary"]["caveat"]


def test_the_caveat_is_derived_from_what_the_skill_declares(tmp_path, monkeypatch):
    mod = _load_bench_run()
    suite = _suite(15, 15)
    everything = [s["key"] for s in suite]
    cases = {
        "whole": ({"drafted_by": "the old author", "drafted_from": everything},
                  ["15 of the 15 scored tasks", "CONTAMINATED"]),
        "drafting": ({"drafted_by": "the control's author",
                      "drafted_from": everything[:15],
                      "exposure": "read the drafting split only"},
                     ["none of the 15 scored tasks", "read the drafting split only"]),
        "undeclared": ({"drafted_by": "someone"}, ["NO declared drafting set"]),
        "missing": (None, ["NO PROVENANCE"]),
    }
    for sid, (prov, expected) in cases.items():
        ctx = _Ctx(tmp_path / sid, min_paired=1, min_discordant=1)
        (tmp_path / sid).mkdir()
        if prov is not None:
            _declare(ctx, sid, prov)
        _wire(mod, monkeypatch, ctx, suite,
              lambda c, s, spec, seed=None: _seed(c, s, {spec.key: (0, 1)}))
        assert mod.cmd_paired(_args(sid, split="eval")) == 0
        cav = json.loads((tmp_path / sid / "calibration.json").read_text(
            encoding="utf-8"))["primary"]["caveat"]
        for phrase in expected:
            assert phrase in cav, f"{sid}: {cav}"
        assert "entire suite" not in cav, f"{sid}: the old assumption is back: {cav}"


def test_an_underpowered_secondary_still_reports_direction(tmp_path, monkeypatch):
    mod = _load_bench_run()
    suite = _suite(15, 15)
    ctx = _Ctx(tmp_path, min_discordant=10)
    outcomes = {s["key"]: ((0, 1) if i < 11 else (1, 1))
                for i, s in enumerate(suite)}

    _wire(mod, monkeypatch, ctx, suite,
          lambda c, sid, spec, seed=None: _seed(c, sid, {spec.key: outcomes[spec.key]}))
    assert mod.cmd_paired(_args("sk2")) == 0

    sec = json.loads(
        (tmp_path / "calibration.json").read_text(encoding="utf-8"))["secondary"]
    assert sec["verdict"] == "insufficient"
    assert sec["n"] == 15
    for field in ("b", "c", "lift_pp", "discordant"):
        assert field in sec, "an underpowered verdict must still show direction"


def test_resume_skips_only_complete_pairs(tmp_path, monkeypatch):
    mod = _load_bench_run()
    suite = _suite(2, 1)
    ctx = _Ctx(tmp_path, min_paired=1, min_discordant=1)

    for arm in ("control", "skill"):
        ctx.conn.execute("INSERT INTO skill_trials VALUES (?,?,?,?,?,?,?,?,?)",
                         ("sk3", "D00", arm, 1, 1.0, 0, 0, 0, _t.time()))
    ctx.conn.execute("INSERT INTO skill_trials VALUES (?,?,?,?,?,?,?,?,?)",
                     ("sk3", "D01", "control", 1, 1.0, 0, 0, 0, _t.time()))
    ctx.conn.commit()

    ran: list[str] = []
    _wire(mod, monkeypatch, ctx, suite,
          lambda c, sid, spec, seed=None: ran.append(spec.key))
    assert mod.cmd_paired(_args("sk3", resume=True)) == 0
    assert "D00" not in ran, "a complete pair should not be re-run"
    assert "D01" in ran, "a half-finished pair MUST be re-run"
    assert "V00" in ran


def test_a_skill_that_hurts_is_rejected_not_quietly_promoted(tmp_path, monkeypatch):
    mod = _load_bench_run()
    suite = _suite(15, 15)
    ctx = _Ctx(tmp_path)
    outcomes = {s["key"]: ((1, 0) if i < 12 else (1, 1))
                for i, s in enumerate(suite)}

    _wire(mod, monkeypatch, ctx, suite,
          lambda c, sid, spec, seed=None: _seed(c, sid, {spec.key: outcomes[spec.key]}))
    assert mod.cmd_paired(_args("sk4")) == 0

    primary = json.loads(
        (tmp_path / "calibration.json").read_text(encoding="utf-8"))["primary"]
    assert primary["verdict"] == "reject"
    assert primary["lift_pp"] < 0, "a harmful skill must show negative lift"


def test_split_eval_restricts_the_trials_that_run(tmp_path, monkeypatch):
    mod = _load_bench_run()
    suite = _suite(15, 15)
    ctx = _Ctx(tmp_path, min_paired=1, min_discordant=1)

    ran: list[str] = []
    _wire(mod, monkeypatch, ctx, suite,
          lambda c, sid, spec, seed=None: ran.append(spec.key))
    assert mod.cmd_paired(_args("sk5", split="eval")) == 0
    assert all(k.startswith("V") for k in ran), f"drafting tasks leaked: {ran}"
    assert len(ran) == 15


def test_the_calibration_takes_the_deputy_out_like_the_noise_floor(tmp_path, monkeypatch, capsys):
    mod = _load_bench_run()
    suite = _suite(1, 1)
    ctx = _Ctx(tmp_path, min_paired=1, min_discordant=1)

    class Autonomy:
        auto_review = True
        approve_scoped_writes = False

    ctx.config.autonomy = Autonomy()
    _wire(mod, monkeypatch, ctx, suite, lambda c, sid, spec, seed=None: None)

    args = _args("sk-deputy")
    args.auto_review = False
    assert mod.cmd_paired(args) == 0

    assert ctx.config.autonomy.auto_review is False, \
        "the deputy was left in the loop; the arms would not be comparable"
    assert ctx.config.autonomy.approve_scoped_writes is True
    assert "auto-review OFF" in capsys.readouterr().out


def test_an_exception_mid_run_releases_the_measurement_lock(tmp_path, monkeypatch):
    import pytest
    from agent.runlock import RunLock, measurement_lock_path

    mod = _load_bench_run()
    ctx = _Ctx(tmp_path, min_paired=1, min_discordant=1)

    def defect(c, sid, spec, seed=None):
        raise RuntimeError("a defect mid-run")

    _wire(mod, monkeypatch, ctx, _suite(1, 1), defect)
    with pytest.raises(RuntimeError, match="a defect mid-run"):
        mod.cmd_paired(_args("sk-exc"))
    probe = RunLock(measurement_lock_path(Path("config.local.yaml").resolve().parent),
                    "probe").acquire()
    probe.release()


def test_a_backend_error_skips_the_pair_rather_than_half_recording_it(tmp_path, monkeypatch, capsys):
    from agent.models.base import BackendError

    mod = _load_bench_run()
    suite = _suite(2, 1)
    ctx = _Ctx(tmp_path, min_paired=1, min_discordant=1)

    class Autonomy:
        auto_review = False
        approve_scoped_writes = True

    ctx.config.autonomy = Autonomy()

    attempted = []

    def sometimes(c, sid, spec, seed=None):
        attempted.append(spec.key)
        if spec.key == "D00":
            raise BackendError("ollama call failed: 500")
        _seed(c, sid, {spec.key: (0, 1)})

    _wire(mod, monkeypatch, ctx, suite, sometimes)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    args = _args("sk-boom")
    args.auto_review = False
    assert mod.cmd_paired(args) == 0

    out = capsys.readouterr().out
    assert "SKIPPED" in out and "D00" in out
    assert attempted.count("D00") == 2, "the pair should be retried once"
    assert "D01" in attempted, "one failure must not end the run"
    assert "V00" in attempted
