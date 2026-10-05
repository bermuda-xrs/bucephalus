"""Deterministic verification for H25. No model in the loop.

PASS/FAIL AND SCORE ARE COMPUTED SEPARATELY, ON PURPOSE.

`passed` is exactly what it always was: every key within TOL. The score does
not and must not influence it. An earlier scorer, max(0, 1 - rel/TOL), was a
STEP FUNCTION outside tolerance - every wrong answer scored exactly 0.0, so
the M7 ratchet could not tell a 3% miss from a 300% miss and `plateau` fired
after three identical zeros. Measured on a 30-task pass: exactly one task
ever moved its score.

The score below is monotonic in log(rel) outside the band, so the ratchet has
an ordering to climb. It is asymptotic and never reaches 0, which is why
`passed` is computed from the tolerance directly - deriving it from the score
would make every answer pass, which would be tuning the threshold to
manufacture a result rather than measuring one.
"""
import json, math, pathlib

EXPECTED = {'v_terminal_m_s': 363.1}
TOL = 0.02            # relative
EXACT_KEYS = ()   # compared exactly rather than by tolerance


def _score(rel):
    """1.0 at exact, 0.5 at the tolerance edge, asymptotic to 0 beyond."""
    if rel <= 0:
        return 1.0
    if rel < TOL:
        return 1.0 - 0.5 * rel / TOL
    return 0.5 / (1.0 + math.log10(rel / TOL))


def check():
    p = pathlib.Path("output/answer.json")
    if not p.exists():
        return False, 0.0
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False, 0.0
    if not isinstance(got, dict):
        return False, 0.0

    scores, ok = [], True
    for k, exp in EXPECTED.items():
        if k not in got:
            return False, 0.0
        if k in EXACT_KEYS:
            hit = (isinstance(got[k], list) and len(got[k]) == len(exp)
                   and all(type(a) is type(b) and a == b for a, b in zip(got[k], exp))
                   if isinstance(exp, list) else type(got[k]) is type(exp) and got[k] == exp)
            scores.append(1.0 if hit else 0.0)
            ok = ok and hit
            continue
        try:
            v = float(got[k])
        except (TypeError, ValueError):
            return False, 0.0
        rel = abs(v - exp) / abs(exp) if exp else abs(v)
        ok = ok and (rel < TOL)
        scores.append(_score(rel))

    return ok, sum(scores) / len(scores)
