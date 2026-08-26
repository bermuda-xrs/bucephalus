"""The `aero_calc` benchmark suite — 30 tasks, deterministic verification.

The suite is a table, not 120 hand-maintained files. Every task is generated
from one row, so a tolerance or a constant can be changed in one place and the
tree regenerated; hand-editing 30 copies of `verify.py` is how a suite quietly
drifts out of agreement with itself.

Two splits, and the separation is load-bearing:

  * **drafting** (15) — a skill generator may look at these.
  * **eval** (15) — never enters a drafting prompt, a provenance record, or
    any log a generator reads. Held out as strictly as M7.2's held-out tests,
    because a skill drafted from a task it is later scored on manufactures a
    lift that is not there.

Emit with:  python bench/suites/aero_calc.py --out bench/tasks/aero_calc
Load with:  python bench/suites/aero_calc.py --load --config config.local.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

CONSTANTS = {
    "g0": 9.80665,
    "earth": {"mu": 3.986004418e14, "radius": 6371000.0, "orbital_v": 29785.0,
              "period_days": 365.256},
    "mars": {"mu": 4.282837e13, "radius": 3389500.0, "orbital_v": 24077.0,
             "period_days": 686.980},
    "moon": {"mu": 4.9048695e12, "radius": 1737400.0},
    "sun": {"mu": 1.327124400e20},
    "au": 1.495978707e11,
    "r_earth_orbit": 1.496e11,
    "r_mars_orbit": 2.279e11,
    "geo_radius": 42164000.0,
}

OBJECTIVE = """\
Read input/params.json and constants.json. Compute the requested value.
Write output/answer.json containing exactly these keys: {keys}.
All outputs in SI base units unless the key name says otherwise.

{detail}"""


@dataclass
class Task:
    id: str
    split: str          # drafting | eval
    tier: str           # easy | medium | hard
    detail: str
    params: dict
    expected: dict
    tol: float          # relative
    trap: str = ""
    exact_keys: tuple[str, ...] = ()   # compared exactly, not by tolerance
    seconds: int = 600
    keys: list[str] = field(default_factory=list)
    extra_files: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.keys:
            self.keys = list(self.expected)


# --------------------------------------------------------------------------
# DRAFTING SPLIT — 15
# --------------------------------------------------------------------------
DRAFTING = [
    Task("E01", "drafting", "easy",
         "Circular orbital velocity for a satellite at the given altitude.",
         {"body": "earth", "altitude_km": 400}, {"v_circular_m_s": 7672.6}, 0.005,
         "altitude is not orbital radius"),
    Task("E02", "drafting", "easy",
         "Orbital period, reported in minutes.",
         {"body": "earth", "altitude_km": 550}, {"period_minutes": 95.5}, 0.005,
         "radius, and seconds to minutes"),
    Task("E03", "drafting", "easy",
         "Escape velocity at the surface of the given body.",
         {"body": "mars"}, {"v_escape_m_s": 5027.0}, 0.005,
         "Mars problem, not Earth constants"),
    Task("E04", "drafting", "easy",
         "Ideal delta-v of a single stage by the Tsiolkovsky equation.",
         {"isp_s": 311, "m0_kg": 549054, "mf_kg": 137000},
         {"delta_v_m_s": 4233.9}, 0.005, "Isp in seconds needs g0"),
    Task("E05", "drafting", "easy",
         "Dynamic pressure at the given speed and density.",
         {"v_km_h": 900, "rho_kg_m3": 0.4135}, {"q_pa": 12922.0}, 0.005,
         "km/h to m/s"),

    Task("M11", "drafting", "medium",
         "First burn only of a Hohmann transfer between two circular orbits.",
         {"body": "earth", "altitude1_km": 400, "altitude2_km": 2000},
         {"delta_v1_m_s": 395.2}, 0.01, "both radii; transfer semi-major axis"),
    Task("M12", "drafting", "medium",
         "Total Hohmann delta-v from LEO to GEO. Use geo_radius directly.",
         {"body": "earth", "altitude1_km": 400, "r2_m": 42164000.0},
         {"delta_v_total_m_s": 3856.7}, 0.01,
         "one radius from altitude, one given directly"),
    Task("M13", "drafting", "medium",
         "Total delta-v of a two-stage vehicle. Stage 2 ignites after stage 1 "
         "separates; discarded structure does not travel with stage 2.",
         {"stage1": {"m0_kg": 500000, "mf_kg": 180000, "isp_s": 300},
          "stage2": {"m0_kg": 150000, "mf_kg": 40000, "isp_s": 340}},
         {"delta_v_total_m_s": 7412.8}, 0.01, "staging bookkeeping"),
    Task("M14", "drafting", "medium",
         "Reynolds number. The viscosity given is dynamic, not kinematic.",
         {"rho_kg_m3": 0.4135, "mu_visc_pa_s": 1.4577e-5, "v_m_s": 250,
          "length_m": 3}, {"reynolds": 2.1275e7}, 0.02, "dynamic viscosity"),
    Task("M15", "drafting", "medium",
         "Semi-major axis of an Earth orbit with the given period.",
         {"body": "earth", "period_minutes": 100},
         {"semi_major_axis_m": 7.138e6}, 0.005, "minutes to seconds"),
    Task("M16", "drafting", "medium",
         "Velocity at apogee of a transfer orbit, by vis-viva.",
         {"body": "earth", "perigee_altitude_km": 300, "apogee_altitude_km": 35786},
         {"v_apogee_m_s": 1607.4}, 0.01, "two altitudes, both need radius"),

    Task("H23", "drafting", "hard",
         "Total delta-v for a Hohmann transfer to GEO with the plane change "
         "combined into the apogee burn.",
         {"body": "earth", "altitude1_km": 400, "r2_m": 42164000.0,
          "inclination_deg": 28.5},
         {"delta_v_total_m_s": 4223.9}, 0.01,
         "degrees to radians; a combined burn is not the sum", seconds=900),
    Task("H24", "drafting", "hard",
         "Total delta-v of a three-stage vehicle.",
         {"stage1": {"m0_kg": 3000000, "mf_kg": 1200000, "isp_s": 282},
          "stage2": {"m0_kg": 1000000, "mf_kg": 350000, "isp_s": 421},
          "stage3": {"m0_kg": 250000, "mf_kg": 80000, "isp_s": 421}},
         {"delta_v_total_m_s": 11572.5}, 0.01, "", seconds=900),
    Task("H25", "drafting", "hard",
         "Terminal velocity of a descending body at the given local conditions.",
         {"m_kg": 500, "cd": 1.0, "area_m2": 4, "rho_kg_m3": 0.01841,
          "g_m_s2": 9.71}, {"v_terminal_m_s": 363.1}, 0.02, "", seconds=900),
    Task("H26", "drafting", "hard",
         "Launch window arithmetic. Compute the synodic period from the two "
         "orbital periods, then the Hohmann transfer time from the two orbital "
         "radii. days_to_arrival is measured from the next window.",
         {"days_since_last_window": 300, "body1": "earth", "body2": "mars"},
         {"days_to_next_window": 479.9, "days_to_arrival": 738.8}, 0.01,
         "reciprocal synodic formula; transfer is half a period", seconds=900),
]

# --------------------------------------------------------------------------
# EVAL SPLIT — 15. Never shown to a generator.
# --------------------------------------------------------------------------
EVAL = [
    Task("E06", "eval", "easy", "Thrust from mass flow and exhaust velocity.",
         {"mdot_kg_s": 280, "ve_m_s": 3050}, {"thrust_n": 854000.0}, 0.005),
    Task("E07", "eval", "easy", "Specific impulse from thrust and mass flow.",
         {"thrust_n": 934000, "mdot_kg_s": 331}, {"isp_s": 287.7}, 0.005, "g0"),
    Task("E08", "eval", "easy", "Surface gravity of the given body.",
         {"body": "moon"}, {"g_surface_m_s2": 1.6249}, 0.005, "Moon constants"),
    Task("E09", "eval", "easy", "Mass flow from thrust and specific impulse.",
         {"thrust_n": 7600, "isp_s": 450}, {"mass_flow_kg_s": 1.7222}, 0.005, "g0"),
    Task("E10", "eval", "easy", "Circular orbit velocity at the given altitude.",
         {"body": "moon", "altitude_km": 100}, {"v_circular_m_s": 1633.9}, 0.005,
         "Moon mu and radius"),

    Task("M17", "eval", "medium", "Synodic period between the two bodies, in days.",
         {"body1": "earth", "body2": "mars"}, {"synodic_period_days": 779.9}, 0.005,
         "reciprocals, not a difference"),
    Task("M18", "eval", "medium",
         "Final-to-initial mass fraction for the given delta-v.",
         {"delta_v_m_s": 9000, "isp_s": 350}, {"mass_fraction": 0.07262}, 0.01),
    Task("M19", "eval", "medium", "Lift coefficient required for level flight.",
         {"weight_n": 50000, "wing_area_m2": 25, "rho_kg_m3": 0.7364, "v_m_s": 180},
         {"cl": 0.1676}, 0.01),
    Task("M20", "eval", "medium", "Delta-v for a pure inclination change at GEO.",
         {"r_m": 42164000.0, "body": "earth", "delta_i_deg": 28.5},
         {"delta_v_m_s": 1513.7}, 0.01, "degrees to radians; half angle"),
    Task("M21", "eval", "medium",
         "Radius of Earth's sphere of influence about the Sun.",
         {"a_m": 1.496e11, "m_body_kg": 5.972e24, "m_primary_kg": 1.989e30},
         {"r_soi_m": 9.25e8}, 0.01, "exponent is 2/5"),
    Task("M22", "eval", "medium",
         "Hohmann transfer time from Earth's orbit to Mars's orbit, in days.",
         {"r1_m": 1.496e11, "r2_m": 2.279e11, "primary": "sun"},
         {"transfer_days": 258.9}, 0.01, "half the period; seconds to days"),

    Task("H27", "eval", "hard",
         "Read input/telemetry.csv. Flag every row where the implied "
         "acceleration exceeds 60 m/s^2, or where specific orbital energy "
         "(v^2/2 - mu/r, with r = body radius + altitude) increases after "
         "thrust cutoff at t = 150 s. Treat an energy change as an increase "
         "only if it exceeds energy_tol_rel relative to the previous row's "
         "magnitude; smaller changes are measurement rounding. Compare each "
         "row with the one before it. Report sorted row indices, 0-based, "
         "not counting the header.",
         {"cutoff_s": 150, "accel_limit_m_s2": 60.0, "body": "earth",
          "energy_tol_rel": 1e-6},
         {"bad_row_indices": [12, 21, 30, 34]}, 0.0,
         "non-uniform timesteps; energy needs altitude and velocity",
         exact_keys=("bad_row_indices",), seconds=1200),
    Task("H28", "eval", "hard",
         "Heliocentric delta-v from a gravity assist with the given hyperbolic "
         "excess speed and turn angle.",
         {"v_inf_m_s": 5000, "turn_angle_deg": 60, "planet_v_m_s": 24077},
         {"delta_v_heliocentric_m_s": 5000.0}, 0.01,
         "degrees; the planet's own velocity is a distractor", seconds=900),
    Task("H29", "eval", "hard",
         "Propellant mass for a sequence of burns, given the DRY mass.",
         {"dry_mass_kg": 2000, "isp_s": 320, "burns_m_s": [1200, 450, 800]},
         {"propellant_mass_kg": 2366.1}, 0.01, "work backwards from dry mass",
         seconds=900),
    # The heliocentric speed convention is PINNED in the objective, and it has
    # to be. constants.json carries mars.orbital_v = 24077, Mars's true mean
    # orbital speed, while a circular orbit at r2 = 2.279e11 moves at
    # sqrt(mu_sun/r2) = 24131.5 — they differ by 0.23% because Mars's orbit is
    # eccentric (e = 0.0934; mean speed = sqrt(mu/a)(1 - e^2/4) recovers 24079).
    # Both numbers are correct facts about different models, and the 0.23% in
    # planet speed amplifies to 2.1% in v_inf and 1.27% in the capture burn.
    # Left unstated, two competent solvers answered 2054.1 and 2080.2 and the
    # continuous scorer ranked one of them at 0.37 — the suite was measuring
    # which convention the model guessed, not whether it can do the physics.
    # A Hohmann transfer is defined between CIRCULAR orbits, so the internally
    # consistent choice is the one derived from the same r1/r2 that give the
    # transfer ellipse; orbital_v is a distractor here and is not used by any
    # other task's answer.
    Task("H30", "eval", "hard",
         "Full delta-v budget: depart LEO, Hohmann to Mars, capture into a "
         "circular Mars orbit at the given altitude. Model both planetary "
         "orbits as circular at r1_m and r2_m: take each planet's heliocentric "
         "speed as sqrt(mu_sun / r), NOT the orbital_v entry in constants.json.",
         {"depart_body": "earth", "depart_altitude_km": 400,
          "capture_body": "mars", "capture_altitude_km": 400,
          "r1_m": 1.496e11, "r2_m": 2.279e11, "primary": "sun"},
         {"delta_v_departure_m_s": 3570.2, "delta_v_capture_m_s": 2080.2,
          "delta_v_total_m_s": 5650.4}, 0.02,
         "hyperbolic excess; both bodies' constants", seconds=1200),
]

ALL = DRAFTING + EVAL


BAD_ROWS = (12, 21, 30, 34)


def telemetry_csv() -> str:
    """H27's input. Exactly four bad rows, at BAD_ROWS.

    The post-cutoff coast must CONSERVE specific orbital energy, or the task
    is unsolvable: a first attempt let altitude keep climbing while velocity
    decayed linearly, which makes e = v^2/2 - mu/r rise on every coasting row.
    A correct solver then flags thirteen rows and the expected answer of four
    is unreachable — the task would have measured nothing but the grader's
    disagreement with physics.

    So after cutoff the altitude is chosen and the velocity is DERIVED from
    the conserved energy. Only the seeded rows break it, and they break it by
    a wide margin so no rounding epsilon can hide or fabricate a violation.

    Timesteps stay non-uniform on purpose: a finite difference that assumes a
    constant dt gets the accelerations wrong, which is the intended trap.
    """
    mu, re = CONSTANTS["earth"]["mu"], CONSTANTS["earth"]["radius"]
    rows = ["t_s,altitude_m,velocity_m_s"]
    t, alt, vel = 0.0, 0.0, 0.0
    cutoff, energy = 150.0, None

    for i in range(40):
        dt = 3.0 + (i % 4) * 1.5
        t += dt
        if t <= cutoff:
            # Powered ascent. A seeded row exceeds the 60 m/s^2 limit.
            accel = 95.0 if i in BAD_ROWS else 28.0
            vel += accel * dt
            alt += vel * dt
        else:
            if energy is None:                      # first coasting row
                energy = 0.5 * vel ** 2 - mu / (re + alt)
            alt += vel * dt                         # keep climbing
            r = re + alt
            inner = 2.0 * (energy + mu / r)         # v from conserved energy
            vel = (inner ** 0.5) if inner > 0 else 0.0
            if i in BAD_ROWS:
                # Energy must not rise while coasting. Make it rise, loudly.
                vel += 400.0
                energy = 0.5 * vel ** 2 - mu / r    # the new, wrong baseline
        # Six decimals, not three. At v ~ 5 km/s a rounding error of 5e-4 m/s
        # perturbs specific energy by ~2 J/kg, which is enough for a solver
        # comparing energies exactly to flag four extra rows. Precision plus
        # the stated tolerance in params.json is what makes the answer unique.
        rows.append(f"{t:.6f},{alt:.6f},{vel:.6f}")
    return "\n".join(rows) + "\n"


VERIFY_TEMPLATE = '''\
"""Deterministic verification for {tid}. No model in the loop."""
import json, pathlib

EXPECTED = {expected!r}
TOL = {tol!r}            # relative
EXACT_KEYS = {exact!r}   # compared exactly rather than by tolerance


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
'''


def emit(out_dir: Path) -> int:
    """Write the whole tree. Idempotent — regenerate freely."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "constants.json").write_text(
        json.dumps(CONSTANTS, indent=2), encoding="utf-8")

    for t in ALL:
        d = out_dir / t.split / t.id
        (d / "input").mkdir(parents=True, exist_ok=True)
        (d / "output").mkdir(parents=True, exist_ok=True)

        (d / "input" / "params.json").write_text(
            json.dumps(t.params, indent=2), encoding="utf-8")
        if t.id == "H27":
            (d / "input" / "telemetry.csv").write_text(
                telemetry_csv(), encoding="utf-8")
        for name, body in t.extra_files.items():
            (d / name).write_text(body, encoding="utf-8")

        (d / "constants.json").write_text(
            json.dumps(CONSTANTS, indent=2), encoding="utf-8")
        (d / "verify.py").write_text(
            VERIFY_TEMPLATE.format(tid=t.id, expected=t.expected, tol=t.tol or 1e-9,
                                   exact=t.exact_keys),
            encoding="utf-8")
        (d / "task.yaml").write_text(
            f"id: {t.id}\nfamily: aero_calc\nsplit: {t.split}\ntier: {t.tier}\n"
            f"objective: |\n  "
            + OBJECTIVE.format(keys=", ".join(t.keys), detail=t.detail).replace("\n", "\n  ")
            + f"\noutput_keys: [{', '.join(t.keys)}]\n"
              f"budget: {{usd: 0.0, seconds: {t.seconds}}}\n"
              f"artifact_scope: [\"input/\", \"output/\", \"constants.json\"]\n",
            encoding="utf-8")
        (d / "meta.yaml").write_text(
            f"tier: {t.tier}\ntrap: {t.trap or 'none'}\nsplit: {t.split}\n"
            f"tolerance: {t.tol}\n", encoding="utf-8")
    return len(ALL)


def as_suite_tasks(split: str | None = None) -> list:
    """The suite as SuiteTask records, for loading into the console database."""
    from agent.userdata.store import SuiteTask

    out = []
    for t in ALL:
        if split and t.split != split:
            continue
        fixtures = {
            f"work/{t.id}/input/params.json": json.dumps(t.params, indent=2),
            f"work/{t.id}/constants.json": json.dumps(CONSTANTS, indent=2),
            f"work/{t.id}/verify.py": VERIFY_TEMPLATE.format(
                tid=t.id, expected=t.expected, tol=t.tol or 1e-9, exact=t.exact_keys),
            # The output directory must exist. Without it the answer write
            # dies with FileNotFoundError and the task measures whether the
            # model remembered to mkdir, which is not what it is for.
            f"work/{t.id}/output/.keep": "",
        }
        if t.id == "H27":
            fixtures[f"work/{t.id}/input/telemetry.csv"] = telemetry_csv()

        # Exact paths, spelled out. A small model that has to infer where the
        # inputs live spends its turns guessing filenames instead of doing the
        # calculation, and a task that measures path-guessing is not measuring
        # what this suite is for.
        goal = (
            f"{t.detail}\n\n"
            f"Input file:     work/{t.id}/input/params.json\n"
            f"Constants file: work/{t.id}/constants.json\n"
            f"Write answer:   work/{t.id}/output/answer.json\n\n"
            f"The answer file must be a JSON object containing exactly these "
            f"keys: {', '.join(t.keys)}. Values in SI base units unless the "
            f"key name says otherwise.\n\n"
            # The layout, not the values. Guessing a key name is not the skill
            # under test, and a wrong guess silently yields 0 and then a
            # ZeroDivisionError — a failure that says nothing about physics.
            f"constants.json is nested like this (values omitted):\n"
            f'  {{"g0": ..., "au": ..., "geo_radius": ...,\n'
            f'    "earth": {{"mu":..., "radius":..., "orbital_v":..., "period_days":...}},\n'
            f'    "mars":  {{...same keys...}},\n'
            f'    "moon":  {{"mu":..., "radius":...}},\n'
            f'    "sun":   {{"mu":...}},\n'
            f'    "r_earth_orbit": ..., "r_mars_orbit": ...}}\n\n'
            f"Use run_code to read the inputs, compute, and write the answer "
            f"file in one program. The output directory already exists."
        )
        out.append(SuiteTask(
            task_key=f"{t.id}",
            goal=goal,
            success_criterion="verify_program",
            output_schema={"type": "object",
                           "required": t.keys,
                           "x-tier": t.tier, "x-split": t.split, "x-trap": t.trap},
            artifact_scope=[f"work/{t.id}"],
            fixtures=fixtures,
            budget_usd=0.0,
            budget_seconds=t.seconds,
            catastrophic=False,
        ))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="build or load the aero_calc suite")
    ap.add_argument("--out", default="bench/tasks/aero_calc")
    ap.add_argument("--load", action="store_true",
                    help="load into the console database instead of writing files")
    ap.add_argument("--config", default="config.local.yaml")
    ap.add_argument("--suite-name", default="aero_calc")
    ap.add_argument("--split", choices=["drafting", "eval"],
                    help="load only one split")
    args = ap.parse_args()

    if args.load:
        from agent.context import AgentContext

        ctx = AgentContext.open(args.config)
        suite = (ctx.userdata.get_suite_by_name(args.suite_name)
                 or ctx.userdata.create_suite(
                     args.suite_name,
                     "aerospace calculations; deterministic verification"))
        verifiers = set(ctx.mesh.registry)
        n = 0
        for st in as_suite_tasks(args.split):
            ctx.userdata.upsert_task(suite.id, st, verifiers)
            n += 1
        total = len(ctx.userdata.get_suite(suite.id).tasks)
        print(f"loaded {n} tasks into suite {args.suite_name!r} ({total} total)")
        ctx.close()
        return 0

    n = emit(Path(args.out))
    print(f"wrote {n} tasks to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
