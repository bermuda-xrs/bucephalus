"""The run sheet must refuse to publish an uninterpretable number.

Part 13's reporting rules are enforced in the generator rather than left to
whoever writes the summary, because "state the caveat in the same paragraph
as the number" is exactly the discipline that erodes when a result is good.
"""

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
    """The stop rule, and it must scale with the number of tasks."""
    verdict, notes = report.interpret_noise(_noise(flaky=12))
    assert verdict == "unusable"
    assert "cannot be interpreted" in " ".join(notes)

    # the same 12 against a 60-task suite is under the 8/30 fraction
    verdict2, _ = report.interpret_noise(_noise(n_tasks=60, flaky=12))
    assert verdict2 == "usable"


def test_one_run_per_task_cannot_classify_stability():
    """A single pass produces no flaky classification, and must say so."""
    verdict, notes = report.interpret_noise(_noise(runs_per_task=1, flaky=0))
    assert verdict == "provisional"
    assert "at least two observations" in " ".join(notes)


def test_a_pass_rate_outside_the_band_is_flagged_with_the_reason():
    _, notes = report.interpret_noise(_noise(mean_pass_rate=0.05))
    joined = " ".join(notes)
    assert "outside" in joined
    assert "discordant" in joined, "say WHY a floor effect costs power"


def test_the_contamination_caveat_rides_with_the_number():
    """Same paragraph as the number, never a footnote."""
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
    """A number without the profile that produced it is not checkable."""
    out = report.render(_noise(), _cal())
    assert "local" in out
    assert "cloud backends []" in out


def test_the_report_names_the_arms_that_were_never_measured():
    """Part 13 wants three ways. Omitting the missing ones would make the one
    that ran look like the whole picture."""
    out = report.render(_noise(), _cal())
    assert "three ways" in out.lower()
    assert "bare model" in out
    assert "never measured" in out
    assert "not supported yet" in out or "is supported yet" in out


def test_the_missing_bare_arm_bounds_the_claim():
    """Without a bare arm you cannot separate the scaffold from the model."""
    out = report.render(_noise(), _cal())
    assert "separates what the scaffold contributes" in out


def test_three_ways_still_renders_with_nothing_measured():
    out = report.render(None, None)
    assert "Nothing to report" in out
