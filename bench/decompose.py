"""Phase 2B's instrument: what KIND of wrong is a wrong answer?

A pass rate says how often the suite is failed. It cannot say whether the
failures are the ones the suite was built to provoke. That distinction decides
whether a skill has anything to repair: a units-discipline skill can fix a
km-to-m error and can do nothing at all about unstructured noise.

It also decides how to read McNemar. If every task fails the SAME trap they
are not 30 independent tests - a skill either flips all of them or none, b and
c go to extremes, and the effective n is far below the nominal one.

WHAT THIS DOES

For each failing value it builds candidate numbers from the task's OWN
constants, params and declared trap string, and reports the closest match with
its residual. A candidate must reproduce the written value to within 1e-6
relative to count. That tolerance is deliberately far tighter than the suite's
own 0.5%: a loose match would let almost any number "decompose" into
something, which is the failure mode this tool exists to avoid.

THE GUARD, WHICH MATTERS MORE THAN THE COVERAGE

A task that declares no trap gets NO trap-derived candidates and returns
`unclassifiable`. Five tasks in the suite declare none - E06, H24, H25, M18,
M19 - and they must come back unclassifiable. If they do not, the decomposer
is inventing categories, and a distribution built from an inventing classifier
is worse than no distribution at all.

`generic_scale_note` records, separately and WITHOUT classifying, whether a
plain unit multiple of the expected value would have matched. It is an
observation for a human, never a category. Keeping it out of the
classification is the whole point.

DISCLOSED LIMITATION

The confusion families below are hand-written, not derived. Deriving them from
the trap strings in general is a larger job than this iteration justifies, and
the acceptance guard above is what makes a hand-written list safe: it can fail
to name a trap, but it cannot invent one. This limitation is disclosed in
FINDINGS rather than hidden here.

    python bench/decompose.py --run runs/phase2a-3b-30tasks.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bench.suites.aero_calc import CONSTANTS, DRAFTING, EVAL  # noqa: E402

# How exactly a candidate must reproduce the written value.
#
# 1e-6 was too tight and could not match ANYTHING in the body-swap family.
# Candidates are built as `expected * ratio`, and the suite's `expected`
# values are rounded to about five significant figures - Mars escape velocity
# is tabled as 5027.0 against a true 5027.047. So the achievable residual
# floor is the rounding error of `expected`, around 1e-5 relative, and a gate
# below that floor rejects correct decompositions by construction. E03's real
# failure sat at 9.36e-06: an exact hit on sqrt(2*mu_mars/R_earth), reported
# as unclassifiable purely because the gate was below the noise floor of its
# own inputs.
#
# But it cannot simply be loosened either. Distinct traps can sit very close
# together: for E01, "altitude ignored (r = R)" and "km read as m
# (r = R + 400)" differ by only 3.1e-05, because adding 400 metres to a
# 6,371,000 m radius barely moves the answer. A gate at 1e-4 conflates them.
#
# So the gate is bounded on BOTH sides by quantities that can be computed
# rather than chosen:
#     above  the rounding floor of `expected`      ~9.4e-06  (E03)
#     below  the separation between distinct traps ~3.1e-05  (E01)
# 2e-5 sits between them, and is still 250x TIGHTER than the suite's own 0.5%
# pass tolerance, so it cannot turn a near miss into a trap - a 1% miss stays
# unclassifiable by a factor of 500.
#
# Where those bounds ever cross, no single gate can separate the candidates,
# and the `ambiguous` classification below is the safety net for exactly that
# case: it reports both rather than picking the nearer and implying a
# precision the data does not carry.
RTOL = 2e-5

# Unit confusions, as multipliers. Hand-written and disclosed - see the module
# docstring. Each entry is (name, factor).
SCALES: list[tuple[str, float]] = [
    ("x1000 (km read as m)", 1000.0),
    ("/1000 (m read as km)", 1e-3),
    ("x3.6 (m/s to km/h)", 3.6),
    ("/3.6 (km/h to m/s)", 1 / 3.6),
    ("x60 (minutes to seconds)", 60.0),
    ("/60 (seconds to minutes)", 1 / 60.0),
    ("x3600 (hours to seconds)", 3600.0),
    ("/3600 (seconds to hours)", 1 / 3600.0),
    ("x86400 (days to seconds)", 86400.0),
    ("/86400 (seconds to days)", 1 / 86400.0),
    ("xg0 (Isp seconds without g0)", 9.80665),
    ("/g0", 1 / 9.80665),
    ("x180/pi (radians as degrees)", 180.0 / math.pi),
    ("xpi/180 (degrees as radians)", math.pi / 180.0),
]

BODIES = ("earth", "mars", "moon")


def _flat_params(task) -> dict:
    return dict(task.params or {})


def _radius_candidates(task) -> list[tuple[str, float]]:
    """Radii the model might have formed, from THIS task's own inputs."""
    p = _flat_params(task)
    body = p.get("body")
    out: list[tuple[str, float]] = []
    if not body or body not in CONSTANTS:
        return out
    R = CONSTANTS[body]["radius"]
    alt_km = p.get("altitude_km")
    if alt_km is not None:
        out.append(("altitude ignored (r = R)", R))
        out.append(("km read as m (r = R + alt_km)", R + alt_km))
        out.append(("correct (r = R + alt_km*1000)", R + alt_km * 1000.0))
        out.append(("r = (R + alt_km)*1000", (R + alt_km) * 1000.0))
    return out


def _quantity_family(key: str) -> str | None:
    k = key.lower()
    if k.startswith("v_") or "velocity" in k or "speed" in k:
        return "velocity"
    if "period" in k:
        return "period"
    return None


def _body_swap_candidates(task, key: str, expected: float) -> list[tuple[str, float]]:
    """Wrong body's constants, when the task names a body and a family."""
    p = _flat_params(task)
    body, fam = p.get("body"), _quantity_family(key)
    if not body or body not in CONSTANTS or fam != "velocity":
        return []
    mu_r, R_r = CONSTANTS[body]["mu"], CONSTANTS[body]["radius"]
    alt = (p.get("altitude_km") or 0) * 1000.0
    r_r = R_r + alt
    out = []
    # MIXED constants, not just whole-body swaps. Found by a real failure the
    # first version missed: E03 wrote sqrt(2*mu_mars/R_earth) exactly - the
    # right body's mu against the wrong body's radius - which is precisely
    # what "Mars problem, not Earth constants" is meant to provoke. Swapping
    # mu and radius together could never produce it, so the tool was silent on
    # the trap the suite declares. Under-counting trap-shaped failures pushes
    # the Phase 2B fraction down and can trigger a STOP the data does not
    # support, which is why this was worth fixing rather than logging.
    for mu_body in BODIES:
        for r_body in BODIES:
            if mu_body == body and r_body == body:
                continue
            if mu_body not in CONSTANTS or r_body not in CONSTANTS:
                continue
            mu_o = CONSTANTS[mu_body]["mu"]
            r_o = CONSTANTS[r_body]["radius"] + alt
            if r_o <= 0:
                continue
            # expected scales as sqrt(mu/r) for both circular and escape
            # speed, so the ratio is formula-independent within this family.
            label = (f"wrong body: {mu_body} mu"
                     + ("" if r_body == mu_body else f" with {r_body} radius")
                     + f", not {body}")
            out.append((label, expected * math.sqrt((mu_o / r_o) / (mu_r / r_r))))
    return out


def _trap_candidates(task, key: str, expected: float) -> list[tuple[str, float]]:
    """Concrete numbers, derived from THIS task's declared trap and inputs."""
    trap = (task.trap or "").strip().lower()
    if not trap or trap == "none":
        return []                       # THE GUARD. No declared trap, no names.

    p = _flat_params(task)
    fam = _quantity_family(key)
    cands: list[tuple[str, float]] = []

    mentions_radius = any(w in trap for w in
                          ("radius", "altitude", "radii", "orbital radius"))
    mentions_body = any(w in trap for w in ("mars", "moon", "earth", "body"))
    mentions_units = any(w in trap for w in
                         ("km", "second", "minute", "hour", "day", "unit",
                          "isp", "g0", "degree", "radian"))

    if mentions_radius and fam in ("velocity", "period"):
        body = p.get("body")
        if body in CONSTANTS:
            mu = CONSTANTS[body]["mu"]
            for rname, r in _radius_candidates(task):
                if r <= 0:
                    continue
                if fam == "velocity":
                    # ratio form: works for circular AND escape speed
                    r_ok = CONSTANTS[body]["radius"] + (p.get("altitude_km") or 0) * 1000.0
                    cands.append((rname, expected * math.sqrt(r_ok / r)))
                else:
                    r_ok = CONSTANTS[body]["radius"] + (p.get("altitude_km") or 0) * 1000.0
                    cands.append((rname, expected * (r / r_ok) ** 1.5))

    if mentions_body:
        cands += _body_swap_candidates(task, key, expected)

    if mentions_units:
        cands += [(n, expected * f) for n, f in SCALES]

    # Compounds: a unit slip applied on top of a radius slip is the single
    # most common real failure here - E01's 250.12 is exactly that.
    base = list(cands)
    for bname, bval in base:
        if "r =" in bname or "altitude" in bname or "km read as m" in bname:
            for sname, f in SCALES[:4]:
                cands.append((f"{bname} + {sname}", bval * f))
    return cands


def decompose(task, key: str, expected: float, got: float) -> dict:
    """Classify one written value. Never force-fits."""
    if got is None:
        return {"classification": "no_artifact", "trap": None,
                "residual": None, "generic_scale_note": None}

    best = None
    matches: list[str] = []
    for name, val in _trap_candidates(task, key, expected):
        if val == 0:
            continue
        resid = abs(val - got) / abs(got) if got else float("inf")
        if resid <= RTOL:
            matches.append(name)
        if best is None or resid < best[0]:
            best = (resid, name)

    # Two different traps that both reproduce the value are not one finding.
    # Reporting the nearer as if it were established would invent a precision
    # the data does not carry, so the ambiguity is surfaced instead.
    distinct = {m.split(" + ")[0] for m in matches}
    if len(distinct) > 1:
        return {"classification": "ambiguous", "trap": None,
                "candidates": sorted(matches),
                "residual": best[0] if best else None,
                "generic_scale_note": None}

    # Recorded, never classified - see the module docstring.
    note = None
    for sname, f in SCALES:
        cand = expected * f
        if cand and abs(cand - got) / abs(got) <= RTOL:
            note = f"value equals expected {sname}, but this task declares " \
                   f"{'no trap' if not (task.trap or '').strip() else 'a different trap'}"
            break

    if best is not None and best[0] <= RTOL:
        return {"classification": "trap", "trap": best[1],
                "residual": best[0], "generic_scale_note": note}
    return {"classification": "unclassifiable", "trap": None,
            "residual": (best[0] if best else None),
            "generic_scale_note": note}


def all_tasks() -> dict:
    return {t.id: t for t in (DRAFTING + EVAL)}


def no_trap_tasks() -> list[str]:
    return sorted(t.id for t in (DRAFTING + EVAL)
                  if not (t.trap or "").strip() or (t.trap or "").strip().lower() == "none")


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 2B trap decomposition")
    ap.add_argument("--run", help="a noise.py report; decomposes every failure")
    ap.add_argument("--self-test", action="store_true",
                    help="the acceptance guard: no-trap tasks must be "
                         "unclassifiable, and the known E01 failures must name "
                         "their trap")
    args = ap.parse_args()

    tasks = all_tasks()

    if args.self_test:
        print("no-trap tasks in the suite:", no_trap_tasks())
        ok = True
        for tid in no_trap_tasks():
            t = tasks[tid]
            key = list(t.expected)[0]
            exp = float(t.expected[key])
            # feed values that WOULD decompose for a trap-declaring task
            for probe, label in ((exp * 1000, "expected x1000"),
                                 (exp * 0.001, "expected /1000"),
                                 (exp * 9.80665, "expected xg0")):
                r = decompose(t, key, exp, probe)
                status = "OK " if r["classification"] == "unclassifiable" else "FAIL"
                if r["classification"] != "unclassifiable":
                    ok = False
                print(f"  {status} {tid} {label:18} -> {r['classification']}"
                      + (f"  ({r['trap']})" if r["trap"] else ""))
        print()
        print("known E01 failures must NAME their trap:")
        e01 = tasks["E01"]
        for val, want in ((7909.544108195981, "km read as m"),
                          (250.12174635464578, "(R + alt_km)*1000")):
            r = decompose(e01, "v_circular_m_s", 7672.6, val)
            hit = r["classification"] == "trap"
            ok = ok and hit
            print(f"  {'OK ' if hit else 'FAIL'} {val} -> {r['classification']}"
                  f"  {r['trap']}  resid {r['residual']}")
        print()
        print("ACCEPTANCE:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    if not args.run:
        ap.error("--run or --self-test required")

    rep = json.loads(Path(args.run).read_text(encoding="utf-8"))
    rows = rep.get("tasks") or []
    print(f"{'key':6} {'status':22} {'class':16} trap / residual")
    counts: dict[str, int] = {}
    for row in rows:
        tid = row["key"]
        t = tasks.get(tid)
        if t is None or row.get("passes", 0) > 0:
            continue
        key = list(t.expected)[0]
        exp = float(t.expected[key])
        got = row.get("answer_written")
        r = decompose(t, key, exp, got)
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
        resid = "" if r["residual"] is None else f"(resid {r['residual']:.2e})"
        print(f"{tid:6} {row.get('classification',''):22} "
              f"{r['classification']:16} {r['trap'] or ''} {resid}")
        if r["generic_scale_note"]:
            print(f"       note: {r['generic_scale_note']}")
    print()
    print("distribution:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
