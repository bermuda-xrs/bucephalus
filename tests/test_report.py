from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("bench_report", ROOT / "bench" / "report.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


report = _load()


def _noise(**over):
    d = {"suite": "aero_calc", "n_tasks": 30, "runs_per_task": 3,
         "split": None, "mean_pass_rate": 0.5, "stable_pass": 10,
         "stable_fail": 15, "flaky": 5, "ended_at_step_limit": 4,
         "ended_budget_exhausted": 0, "wall_s_all_runs": 14400,
         "profile": "local",
         "profile_provenance": {"cloud_backends": [], "credentials_present": [],
                                "backends": {"cloud": {"model": "granite4.1:3b"}}},
         "tier_pass_rates": {"easy": 0.8, "medium": 0.5, "hard": 0.2}}
    d.update(over)
    return d


def _cal(**over):
    d = {"date": "2026-08-27", "profile": "local",
         "models": {"cloud": "granite4.1:3b"},
         "primary": {"scope": "all 30 tasks that ran", "verdict": "promote",
                     "reason": "gate passed", "n": 30, "b": 12, "c": 1,
                     "discordant": 13, "lift_pp": 36.67, "p": 0.0017,
                     "caveat": "POSITIVE CONTROL; the author saw the suite."},
         "secondary": {"scope": "eval subset, 15 tasks", "verdict": "promote",
                       "reason": "gate passed", "n": 15, "b": 10, "c": 0,
                       "discordant": 10, "lift_pp": 66.7, "p": 0.001,
                       "caveat": "Contamination-clean but UNDERPOWERED."},
         "caveats": ["Token counts are local estimates."]}
    d.update(over)
    return d


def test_a_missing_noise_floor_is_stated_not_omitted():
    out = report.render(None, _cal())
    assert "Not measured" in out
    assert "uninterpretable" in out


def test_a_missing_calibration_says_rejections_cannot_be_read():
    out = report.render(_noise(), None)
    assert "Not run" in out
    assert "indistinguishable" in out


def test_too_many_flaky_tasks_makes_the_suite_unusable():
    verdict, notes = report.interpret_noise(_noise(flaky=12))
    assert verdict == "unusable"
    assert "cannot be interpreted" in " ".join(notes)

    verdict2, _ = report.interpret_noise(_noise(n_tasks=60, flaky=12))
    assert verdict2 == "usable"


def test_one_run_per_task_cannot_classify_stability():
    verdict, notes = report.interpret_noise(_noise(runs_per_task=1, flaky=0))
    assert verdict == "provisional"
    assert "at least two observations" in " ".join(notes)


def test_a_pass_rate_outside_the_band_is_flagged_with_the_reason():
    _, notes = report.interpret_noise(_noise(mean_pass_rate=0.05))
    joined = " ".join(notes)
    assert "outside" in joined
    assert "discordant" in joined, "say WHY a floor effect costs power"


def test_the_contamination_caveat_rides_with_the_number():
    out = report.render(_noise(), _cal())
    for line in out.splitlines():
        if "lift **36.67pp**" in line:
            assert "POSITIVE CONTROL" in line, \
                "the caveat must be in the same paragraph as the lift"
            break
    else:
        raise AssertionError("the primary lift was never rendered")


def test_both_arms_are_rendered_with_their_own_caveats():
    out = report.render(_noise(), _cal())
    assert "PRIMARY" in out and "SECONDARY" in out
    assert "UNDERPOWERED" in out


def test_the_profile_is_on_the_sheet():
    out = report.render(_noise(), _cal())
    assert "local" in out
    assert "cloud backends []" in out


def test_the_report_names_the_arms_that_were_never_measured():
    out = report.render(_noise(), _cal())
    assert "three ways" in out.lower()
    assert "bare model" in out
    assert "never measured" in out
    assert "not supported yet" in out or "is supported yet" in out


def test_the_missing_bare_arm_bounds_the_claim():
    out = report.render(_noise(), _cal())
    assert "separates what the scaffold contributes" in out


def test_three_ways_still_renders_with_nothing_measured():
    out = report.render(None, None)
    assert "Nothing to report" in out


def test_the_cost_axis_is_seconds_and_says_why():
    out = report.render(_noise(median_wall_s=214), None)
    assert "SECONDS, not dollars" in out
    assert "n/a under local profile" in out, "keep the dollar column, labelled"
    assert "median s/task" in out


def test_the_header_names_every_disabled_component():
    n = _noise()
    n["profile_provenance"]["escalation"] = "NOT WIRED - no call site"
    n["auto_review"] = False
    out = report.render(n, None)
    assert "measured system is not the shipped system" in out
    assert "escalation-by-disagreement" in out
    assert "deputy" in out


def test_a_scaled_budget_is_disclosed():
    out = report.render(_noise(budget_scale=3.0), None)
    assert "budgets scaled" in out
    assert "x3" in out


def _arms(hits_a=1, hits_b=1, n=10):
    return {"arms": [
        {"name": "arm_3b", "budget_s": 600, "ceiling_hits": hits_a,
         "n_tasks": n, "pass_rate": 0.1, "median_wall_s": 200},
        {"name": "arm_8b", "budget_s": 1800, "ceiling_hits": hits_b,
         "n_tasks": n, "pass_rate": 0.4, "median_wall_s": 800},
    ]}


def test_ceiling_hits_are_reported_next_to_every_pass_rate():
    out = report.render(_noise(**_arms()), None)
    assert "Budget censoring" in out
    assert "ceiling hits" in out


def test_a_lopsided_ceiling_count_is_called_confounded():
    out = report.render(_noise(**_arms(hits_a=0, hits_b=7)), None)
    assert "CONFOUNDED" in out
    assert "not attributable" in out
    assert "do not plot them as a clean frontier" in out


def test_comparable_ceilings_are_not_flagged():
    out = report.render(_noise(**_arms(hits_a=1, hits_b=1)), None)
    assert "CONFOUNDED" not in out


def test_mixing_harness_versions_is_refused_not_plotted():
    n = _noise(pareto=[
        {"arm": "old", "harness_version": 1, "pass_rate": 0.1, "median_wall_s": 200},
        {"arm": "new", "harness_version": 2, "pass_rate": 0.8, "median_wall_s": 800},
    ])
    out = report.render(n, None)
    assert "REFUSING TO PLOT" in out
    assert "different harness versions" in out
    assert "| old |" not in out, "the points must not be plotted anyway"


def test_one_harness_version_plots_normally():
    n = _noise(pareto=[
        {"arm": "arm_3b", "harness_version": 2, "pass_rate": 0.1,
         "median_wall_s": 200, "done": 2, "done_unannounced": 1},
        {"arm": "arm_8b", "harness_version": 2, "pass_rate": 0.8,
         "median_wall_s": 800, "done": 5, "done_unannounced": 19},
    ])
    out = report.render(n, None)
    assert "REFUSING TO PLOT" not in out
    assert "| arm_8b |" in out


def test_done_and_done_unannounced_are_never_collapsed():
    n = _noise(pareto=[{"arm": "a", "harness_version": 2, "pass_rate": 0.8,
                        "median_wall_s": 800, "done": 5, "done_unannounced": 19}])
    out = report.render(n, None)
    assert "done_unannounced" in out
    for line in out.splitlines():
        if line.startswith("| a |"):
            assert "| 5 |" in line and "| 19 |" in line
            break
    else:
        raise AssertionError("the arm row was never rendered")


def test_the_header_states_the_harness_version():
    out = report.render(_noise(harness_version=2), None)
    assert "harness version" in out
    assert "comparable only within one version" in out
