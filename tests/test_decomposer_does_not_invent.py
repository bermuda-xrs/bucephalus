"""The trap decomposer must fail to name, never invent.

Phase 2B branches on the fraction of failures that are trap-shaped, and
Phase 3's reading of McNemar depends on how many DISTINCT traps there are -
if every task fails the same way they are not independent tests, and the
effective n is far below the nominal one.

A classifier that force-fits is therefore worse than no classifier: it would
report a healthy spread of traps that does not exist, and the b/c split would
be read as evidence about a skill when it was an artifact of the tool.

The guard is that a task declaring no trap yields no candidates at all. Five
tasks in the suite declare none. They are fed values that WOULD decompose for
a trap-declaring task, and must still come back unclassifiable.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from bench.decompose import (  # noqa: E402
    RTOL, all_tasks, decompose, no_trap_tasks)

NO_TRAP = ["E06", "H24", "H25", "M18", "M19"]


def test_the_five_no_trap_tasks_are_the_ones_we_think():
    """If the suite changes, this test must be re-derived, not silently pass."""
    assert no_trap_tasks() == NO_TRAP


def test_no_trap_tasks_never_get_a_trap_named():
    """The acceptance guard. Feed them values that decompose elsewhere."""
    tasks = all_tasks()
    for tid in NO_TRAP:
        t = tasks[tid]
        key = list(t.expected)[0]
        exp = float(t.expected[key])
        for factor in (1000.0, 1e-3, 9.80665, 3.6, 60.0):
            r = decompose(t, key, exp, exp * factor)
            assert r["classification"] == "unclassifiable", (
                f"{tid} was force-fitted to {r['trap']!r} for a value that is "
                f"just expected x{factor}. The decomposer is inventing.")
            assert r["trap"] is None


def test_a_generic_scale_is_noted_but_never_classified():
    """The note exists so a human can see it; it must not become a category."""
    tasks = all_tasks()
    t = tasks["E06"]
    key = list(t.expected)[0]
    exp = float(t.expected[key])
    r = decompose(t, key, exp, exp * 1000.0)
    assert r["classification"] == "unclassifiable"
    assert r["generic_scale_note"], (
        "a plain x1000 should still be REPORTED to a human, just not "
        "classified as a trap the suite never declared")


def test_the_known_e01_failures_are_named():
    """The other half: a real trap must actually be recognised."""
    t = all_tasks()["E01"]
    for value, expect_in_name in (
            (7909.544108195981, "km read as m"),
            (250.12174635464578, "*1000"),
    ):
        r = decompose(t, "v_circular_m_s", 7672.6, value)
        assert r["classification"] == "trap", (
            f"{value} is a known, exactly-decomposing failure and was not "
            f"named: {r}")
        assert expect_in_name in r["trap"]
        assert r["residual"] <= RTOL


def test_a_missing_artifact_is_not_a_trap():
    """No answer is a distinct outcome from a wrong answer."""
    t = all_tasks()["E01"]
    r = decompose(t, "v_circular_m_s", 7672.6, None)
    assert r["classification"] == "no_artifact"
    assert r["trap"] is None


def test_an_unrecognisable_number_stays_unclassifiable():
    """The residual gate is tight on purpose: 0.5% is the SUITE's tolerance,
    not a licence for the classifier to call anything close a match."""
    t = all_tasks()["E01"]
    r = decompose(t, "v_circular_m_s", 7672.6, 6123.4567)
    assert r["classification"] == "unclassifiable"


def test_a_near_miss_is_not_snapped_to_a_trap():
    """A value 1% away from a real trap value must NOT be claimed as it."""
    t = all_tasks()["E01"]
    near = 7909.544108195981 * 1.01
    r = decompose(t, "v_circular_m_s", 7672.6, near)
    assert r["classification"] == "unclassifiable", (
        "a 1% miss was snapped onto the km-read-as-m trap; the residual gate "
        "is not doing its job")
