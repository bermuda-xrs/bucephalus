"""Deterministic verification for E09. No model in the loop."""
import json, pathlib

EXPECTED = {'mass_flow_kg_s': 1.7222}
TOL = 0.005            # relative
EXACT_KEYS = ()   # compared exactly rather than by tolerance


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

    scores = []
    for k, exp in EXPECTED.items():
        if k not in got:
            return False, 0.0
        if k in EXACT_KEYS:
            # A list of indices is right or it is not; there is no "close".
            ok = list(got[k]) == list(exp) if isinstance(exp, list) else got[k] == exp
            scores.append(1.0 if ok else 0.0)
            continue
        try:
            v = float(got[k])
        except (TypeError, ValueError):
            return False, 0.0
        rel = abs(v - exp) / abs(exp) if exp else abs(v)
        # Full marks at exact, zero at the tolerance edge: the ratchet needs
        # to know that a near miss beat a wild one.
        scores.append(max(0.0, 1.0 - rel / TOL))

    passed = all(s > 0.0 for s in scores)
    return passed, sum(scores) / len(scores)
