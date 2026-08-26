"""The calibration run end to end, on scripted outcomes.

This is the run that costs hours on real models. Every blocker it could hit is
cheaper to find here: running a scripted version first caught four separate
blockers in one session, including a deputy that denied 100% of actions and
would have reported 0% on every task.

So this drives `cmd_paired` itself - the split filter, the trial loop, both
analyses, the printed caveats, the report file - rather than testing the gate
in isolation.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time as _t
from pathlib import Path

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
    """A context with a real skill_trials table and nothing else expensive."""

    def __init__(self, tmp_path, min_paired=15, min_discordant=10):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE skill_trials(skill_id TEXT, task_key TEXT, arm TEXT,"
            " success INT, score REAL, paid_tokens INT, free_tokens INT,"
            " wall_ms INT, created_at REAL)")
        self.conn.execute(
            "CREATE TABLE semantic_cache(key TEXT PRIMARY KEY, value_json TEXT,"
            " taint TEXT, source_hash TEXT, created_at REAL, hits INT)")
        gate = type("G", (), {"min_paired_trials": min_paired,
                              "min_discordant_pairs": min_discordant,
                              "min_lift_pp": 5.0, "max_p_value": 0.05})()
        self.config = type("C", (), {
            "gate": gate, "profile": "local", "profile_provenance": {},
            "resolve": staticmethod(lambda x: str(tmp_path / Path(x).name)),
        })()
        self.backends = {"cloud": type("B", (), {
            "cfg": type("BC", (), {"model": "mock-3b"})()})()}
        self.mesh = type("M", (), {
            "suspicion_signal": staticmethod(lambda v, h: None)})()
        self.closed = False

    def close(self):
        self.closed = True


def _seed(ctx, skill_id, outcomes):
    """outcomes: {task_key: (control_success, skill_success)}"""
    for key, (c, s) in outcomes.items():
        for arm, val in (("control", c), ("skill", s)):
            ctx.conn.execute(
                "INSERT INTO skill_trials VALUES (?,?,?,?,?,?,?,?,?)",
                (skill_id, key, arm, val, float(val), 0, 100, 1000, _t.time()))
    ctx.conn.commit()


def _args(skill_id, split=None, resume=False):
    return type("A", (), {"config": "config.local.yaml", "profile": "local",
                          "suite": "aero_calc", "split": split,
                          "skill_id": skill_id, "out": "calibration.json",
                          "resume": resume})()


def _wire(mod, monkeypatch, ctx, suite, on_trial):
    monkeypatch.setattr(mod, "AgentContext", type("A", (), {
        "open": staticmethod(lambda c, profile=None: ctx)}))
    monkeypatch.setattr(mod, "load_suite", lambda spec, ctx=None: suite)
    monkeypatch.setattr(mod, "run_paired_trial", on_trial)


def test_the_whole_calibration_path_reports_two_numbers(tmp_path, monkeypatch, capsys):
    """A real effect on all 30, reported alongside the eval-15 subset.

    This is the shape the calibration should produce if the instrument works.
    """
    mod = _load_bench_run()
    suite = _suite(15, 15)
    ctx = _Ctx(tmp_path)

    outcomes = {}
    for i, s in enumerate(suite):
        if i < 12:
            outcomes[s["key"]] = (0, 1)      # skill wins
        elif i == 12:
            outcomes[s["key"]] = (1, 0)      # control wins
        else:
            outcomes[s["key"]] = (1, 1)      # concordant pass

    _wire(mod, monkeypatch, ctx, suite,
          lambda c, sid, spec, seed=None: _seed(c, sid, {spec.key: outcomes[spec.key]}))
    assert mod.cmd_paired(_args("sk1")) == 0

    out = capsys.readouterr().out
    assert "PRIMARY" in out and "SECONDARY" in out
    assert "POSITIVE CONTROL" in out, "the contamination caveat must be printed"
    assert "UNDERPOWERED" in out

    report = json.loads((tmp_path / "calibration.json").read_text(encoding="utf-8"))
    assert report["primary"]["n"] == 30
    assert report["primary"]["b"] == 12 and report["primary"]["c"] == 1
    assert report["primary"]["verdict"] == "promote"
    assert report["secondary"]["n"] == 15, "eval subset comes from the same trials"
    assert report["profile"] == "local"
    assert "author" in report["primary"]["caveat"]


def test_an_underpowered_secondary_still_reports_direction(tmp_path, monkeypatch):
    """A verdict of insufficient that reports only n hides where it pointed."""
    mod = _load_bench_run()
    suite = _suite(15, 15)
    ctx = _Ctx(tmp_path, min_discordant=10)
    # every win lands in the drafting half; the eval half is quiet
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
    """A half-finished pair is dropped by the analysis, so it must be re-run."""
    mod = _load_bench_run()
    suite = _suite(2, 1)
    ctx = _Ctx(tmp_path, min_paired=1, min_discordant=1)

    for arm in ("control", "skill"):                 # D00 complete
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
    """The outcome the literature says to expect. It has to be visible."""
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
    """Generated skills stay eval-only, so the filter must bite before trials."""
    mod = _load_bench_run()
    suite = _suite(15, 15)
    ctx = _Ctx(tmp_path, min_paired=1, min_discordant=1)

    ran: list[str] = []
    _wire(mod, monkeypatch, ctx, suite,
          lambda c, sid, spec, seed=None: ran.append(spec.key))
    assert mod.cmd_paired(_args("sk5", split="eval")) == 0
    assert all(k.startswith("V") for k in ran), f"drafting tasks leaked: {ran}"
    assert len(ran) == 15
