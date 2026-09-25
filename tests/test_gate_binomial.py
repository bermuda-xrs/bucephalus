"""The gate's McNemar p-value without scipy (Phase F, build size).

scipy - with numpy, ~95 MB of the desktop build - was imported for one call:
`binomtest(b, n, 0.5, alternative="greater")`. At p = 0.5 that is an exact
tail sum of binomial coefficients, which the standard library computes in
integers. Equivalence is checked against scipy itself for every (b, n) up to
n = 60, when scipy is installed, and against exact fractions always.
"""

from __future__ import annotations

from fractions import Fraction
from math import comb

import pytest

from agent.skills.gate import binom_greater


def test_exact_values():
    assert binom_greater(9, 10) == pytest.approx(11 / 1024)
    assert binom_greater(0, 7) == 1.0
    for n in range(1, 30):
        for b in range(n + 1):
            exact = Fraction(sum(comb(n, k) for k in range(b, n + 1)), 2 ** n)
            assert binom_greater(b, n) == pytest.approx(float(exact), rel=1e-12)


def test_matches_scipy_everywhere_it_is_used():
    stats = pytest.importorskip("scipy.stats")
    for n in range(1, 61):
        for b in range(n + 1):
            want = stats.binomtest(b, n, 0.5, alternative="greater").pvalue
            assert binom_greater(b, n) == pytest.approx(want, rel=1e-9, abs=1e-15), (b, n)
