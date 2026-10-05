from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "bench_coverage", ROOT / "bench" / "coverage.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cov = _load()

SKILL = """
## 1. Normalize before computing
km->m, km/h->m/s, minutes->seconds, degrees->radians.
## 2. Identify the body and the radius
Read that body's mu and radius from constants.json. altitude + body radius.
## 3. Expand the compound constants
Isp in seconds is not a velocity: exhaust velocity = Isp * g0.
"""


def _tasks():
    return [
        {"key": "E01", "tier": "easy", "split": "drafting",
         "trap": "altitude is not orbital radius"},
        {"key": "E05", "tier": "easy", "split": "drafting",
         "trap": "km/h to m/s"},
        {"key": "M13", "tier": "medium", "split": "drafting",
         "trap": "staging bookkeeping"},
        {"key": "M21", "tier": "medium", "split": "eval",
         "trap": "exponent is 2/5"},
        {"key": "H29", "tier": "hard", "split": "eval",
         "trap": "work backwards from dry mass"},
    ]


def test_it_finds_the_topics_a_skill_claims():
    topics = cov.skill_topics(SKILL)
    assert topics == {"units", "body/radius", "isp/trig/magnitude"}


def test_a_skill_about_nothing_covers_nothing():
    rows = cov.coverage(_tasks(), cov.skill_topics("# a skill\nTry your best.\n"))
    assert not any(r["covered"] for r in rows)


def test_tasks_outside_the_skill_are_marked_uncovered():
    rows = {r["key"]: r for r in cov.coverage(_tasks(), cov.skill_topics(SKILL))}
    assert rows["E01"]["covered"], "altitude/radius is module 2"
    assert rows["E05"]["covered"], "km/h is module 1"
    assert not rows["M13"]["covered"], "staging bookkeeping is in no module"
    assert not rows["M21"]["covered"], "an exponent rule is in no module"
    assert not rows["H29"]["covered"]


def test_it_says_stop_when_the_floor_is_unreachable():
    rows = cov.coverage(_tasks(), cov.skill_topics(SKILL))
    out = cov.report(rows, min_discordant=10)
    assert "STOP" in out
    assert "cannot clear a floor of 10" in out
    assert "insufficient" in out, "say what the wasted run would report"


def test_it_permits_but_does_not_promise_when_the_floor_is_reachable():
    rows = cov.coverage(_tasks(), cov.skill_topics(SKILL))
    out = cov.report(rows, min_discordant=1)
    assert "STOP" not in out
    assert "upper bound" in out.lower()
    assert "NOT a prediction" in out, "must not be read as a forecast"


def test_the_split_filter_narrows_the_bound():
    rows = cov.coverage(_tasks(), cov.skill_topics(SKILL))
    evl = cov.report(rows, min_discordant=1, split="eval")
    for key in ("M21", "H29"):
        assert key in evl
    assert "E01" not in evl, "drafting tasks must not count toward an eval bound"


def test_the_real_suite_and_the_real_skill_clear_the_floor():
    import yaml  # noqa: F401

    tasks = cov.load_tasks(ROOT / "bench" / "tasks" / "archive" / "aero_calc")
    assert len(tasks) == 30
    skill = (ROOT / "examples" / "skills" / "aero_units_discipline.md"
             ).read_text(encoding="utf-8")
    rows = cov.coverage(tasks, cov.skill_topics(skill))
    covered = sum(1 for r in rows if r["covered"])
    assert covered >= 10, (
        f"only {covered} of 30 tasks are in the skill's scope; the gate floor "
        "of 10 discordant pairs would be unreachable")
