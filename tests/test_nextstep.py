from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "bench_nextstep", ROOT / "bench" / "nextstep.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ns = _load()


def _ledger(tmp_path: Path, rows, sig=None):
    p = tmp_path / "noise-floor.json.partial.jsonl"
    lines = [json.dumps({"_signature": sig or {"suite": "aero_calc"}})]
    lines += [json.dumps(r) for r in rows]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _rows(n, passed, run=0, offset=0):
    return [{"run": run, "key": f"T{i + offset:02d}", "status": "done",
             "passed": i < passed, "wall_s": 100.0} for i in range(n)]


def test_pass_one_in_progress_decides_nothing(tmp_path):
    _ledger(tmp_path, _rows(10, 1))
    s = ns.assess(tmp_path)
    assert s["proceed"] is True
    assert "pass 1 in progress" in s["phase"]
    assert "Nothing to decide" in s["note"]


def test_a_floor_effect_after_pass_one_stops_the_run(tmp_path):
    _ledger(tmp_path, _rows(30, 3))
    s = ns.assess(tmp_path)
    assert s["proceed"] is False
    assert "GATE: difficulty" in s["phase"]
    assert "STOP" in s["note"]
    assert s["next_command"] is None


def test_the_difficulty_gate_states_the_counter_argument(tmp_path):
    _ledger(tmp_path, _rows(30, 3))
    note = ns.assess(tmp_path)["note"]
    assert "BASELINE arm only" in note
    assert "discordance" in note
    assert "sighting shot" in note, "offer the cheap discriminator"


def test_a_pass_rate_inside_the_band_proceeds_to_the_remaining_passes(tmp_path):
    _ledger(tmp_path, _rows(30, 15))
    s = ns.assess(tmp_path)
    assert s["proceed"] is True
    assert "pass 2 of 3" in s["phase"]
    assert "--runs 3 --resume" in s["next_command"]


def test_too_many_flaky_tasks_stops_before_any_calibration(tmp_path):
    rows = _rows(30, 15) + _rows(30, 15, run=1) + _rows(30, 15, run=2)
    _ledger(tmp_path, rows)
    (tmp_path / "noise-floor.json").write_text(json.dumps(
        {"n_tasks": 30, "flaky": 12, "mean_pass_rate": 0.5}), encoding="utf-8")
    s = ns.assess(tmp_path)
    assert s["proceed"] is False
    assert "GATE: flakiness" in s["phase"]
    assert "cannot be interpreted" in s["note"]


def test_a_usable_noise_floor_leads_to_the_calibration(tmp_path):
    rows = _rows(30, 15) + _rows(30, 15, run=1) + _rows(30, 15, run=2)
    _ledger(tmp_path, rows)
    (tmp_path / "noise-floor.json").write_text(json.dumps(
        {"n_tasks": 30, "flaky": 5, "mean_pass_rate": 0.5}), encoding="utf-8")
    s = ns.assess(tmp_path)
    assert s["proceed"] is True
    assert "calibration next" in s["phase"]
    assert "paired" in s["next_command"]
    assert "coverage.py" in s["note"], "check the upper bound before the hours"


def test_a_rejected_calibration_stops_everything(tmp_path):
    rows = _rows(30, 15) + _rows(30, 15, run=1) + _rows(30, 15, run=2)
    _ledger(tmp_path, rows)
    (tmp_path / "noise-floor.json").write_text(json.dumps(
        {"n_tasks": 30, "flaky": 5, "mean_pass_rate": 0.5}), encoding="utf-8")
    (tmp_path / "calibration.json").write_text(json.dumps(
        {"primary": {"verdict": "reject", "lift_pp": -3.0}}), encoding="utf-8")
    s = ns.assess(tmp_path)
    assert s["proceed"] is False
    assert "GATE: instrument" in s["phase"]
    assert "uninterpretable" in s["note"]
    assert "do not accept" in s["note"].lower()


def test_a_promoted_calibration_opens_the_report_step(tmp_path):
    rows = _rows(30, 15) + _rows(30, 15, run=1) + _rows(30, 15, run=2)
    _ledger(tmp_path, rows)
    (tmp_path / "noise-floor.json").write_text(json.dumps(
        {"n_tasks": 30, "flaky": 5, "mean_pass_rate": 0.5}), encoding="utf-8")
    (tmp_path / "calibration.json").write_text(json.dumps(
        {"primary": {"verdict": "promote", "lift_pp": 30.0}}), encoding="utf-8")
    s = ns.assess(tmp_path)
    assert s["proceed"] is True
    assert "report" in s["next_command"]
    assert "eval-only" in s["note"], "generated skills stay eval-only"


def test_a_truncated_ledger_line_is_ignored_not_fatal(tmp_path):
    p = _ledger(tmp_path, _rows(5, 1))
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"run": 0, "key": "T06", "sta')
    s = ns.assess(tmp_path)
    assert s["runs_completed"] == 5


def test_all_runs_done_but_no_report_says_so(tmp_path):
    rows = _rows(30, 15) + _rows(30, 15, run=1) + _rows(30, 15, run=2)
    _ledger(tmp_path, rows)
    s = ns.assess(tmp_path)
    assert s["proceed"] is False
    assert "report missing" in s["phase"]
    assert "--resume" in s["note"]
