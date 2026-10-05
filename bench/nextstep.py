from __future__ import annotations

import argparse
import json
from pathlib import Path

BAND_PASS1 = (0.25, 0.75)
MAX_FLAKY_FRACTION = 8 / 30
TOTAL_PASSES = 3


def _load(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def read_ledger(path: Path) -> tuple[dict, dict]:
    sig, rows = {}, {}
    if not path.exists():
        return sig, rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "_signature" in row:
            sig = row["_signature"]
        else:
            rows[(row["run"], row["key"])] = row
    return sig, rows


def assess(runs_dir: Path, n_tasks: int = 30) -> dict:
    ledger = runs_dir / "noise-floor.json.partial.jsonl"
    _, rows = read_ledger(ledger)
    noise = _load(runs_dir / "noise-floor.json")
    cal = _load(runs_dir / "calibration.json")

    done = len(rows)
    passes_done = done // n_tasks
    in_pass = done % n_tasks
    passed = sum(1 for r in rows.values() if r.get("passed"))
    rate = passed / done if done else 0.0

    state = {
        "runs_completed": done,
        "of": n_tasks * TOTAL_PASSES,
        "passes_complete": passes_done,
        "current_pass_progress": in_pass,
        "observed_pass_rate": round(rate, 4),
        "calibration_done": cal is not None,
    }

    if done < n_tasks:
        state.update(
            phase="pass 1 in progress",
            proceed=True,
            next_command=(
                "python bench/noise.py --config config.local.yaml "
                "--profile local --runs 1 --resume --out runs/noise-floor.json"),
            note=(f"{done}/{n_tasks} tasks measured, {passed} passed so far "
                  f"({rate:.0%}). Nothing to decide until pass 1 completes."),
        )
        return state

    if passes_done < TOTAL_PASSES:
        lo, hi = BAND_PASS1
        if not (lo <= rate <= hi):
            state.update(
                phase="pass 1 complete - GATE: difficulty",
                proceed=False,
                next_command=None,
                note=(
                    f"STOP. Mean pass rate {rate:.1%} is outside the "
                    f"{lo:.0%}-{hi:.0%} band. One pass of {n_tasks} is about "
                    "+/-9pp at 1 SE, so this net only catches gross "
                    "miscalibration - and it caught some. Rebalance "
                    "difficulty before spending the remaining passes.\n"
                    "Counter-argument worth weighing first: the noise floor "
                    "measures the BASELINE arm only. A low baseline with a "
                    "working skill arm is near-maximum discordance, which is "
                    "ideal for McNemar. Cheapest discriminator is a skill-arm "
                    "sighting shot on a few medium/hard tasks before "
                    "rebalancing anything."),
            )
            return state
        state.update(
            phase=f"pass {passes_done + 1} of {TOTAL_PASSES}",
            proceed=True,
            next_command=(
                "python bench/noise.py --config config.local.yaml "
                f"--profile local --runs {TOTAL_PASSES} --resume "
                "--out runs/noise-floor.json"),
            note=(f"Pass rate {rate:.1%} is inside the band. "
                  f"{passes_done} of {TOTAL_PASSES} passes complete."),
        )
        return state

    if noise is None:
        state.update(
            phase="passes complete, report missing",
            proceed=False,
            next_command=None,
            note=("All runs are in the ledger but runs/noise-floor.json was "
                  "never written - the last process was killed before it "
                  "wrote the report. Re-run with --resume to finish it."),
        )
        return state

    flaky = noise.get("flaky", 0)
    limit = max(1, round(MAX_FLAKY_FRACTION * noise.get("n_tasks", n_tasks)))
    if flaky > limit:
        state.update(
            phase="noise floor complete - GATE: flakiness",
            proceed=False,
            next_command=None,
            note=(f"STOP. {flaky} of {noise.get('n_tasks')} tasks are flaky, "
                  f"above the limit of {limit}. A lift measured against this "
                  "much noise cannot be interpreted. Tighten the task "
                  "specifications before evaluating any skill."),
        )
        return state

    if cal is None:
        state.update(
            phase="noise floor usable - calibration next",
            proceed=True,
            next_command=(
                "python bench/run.py --config config.local.yaml --profile "
                "local paired <skill_id> --suite aero_calc --resume "
                "--out runs/calibration.json"),
            note=(f"{flaky} flaky, within the limit of {limit}. Run the "
                  "calibration skill on ALL 30 as a positive control on the "
                  "instrument. Check the upper bound first: "
                  "python bench/coverage.py --skill "
                  "examples/skills/aero_units_discipline.md"),
        )
        return state

    verdict = (cal.get("primary") or {}).get("verdict")
    if verdict != "promote":
        state.update(
            phase="calibration complete - GATE: instrument",
            proceed=False,
            next_command=None,
            note=(f"STOP. The calibration verdict is '{verdict}', not "
                  "'promote'. A gate that rejects everything and a gate that "
                  "works are indistinguishable until a known-good skill "
                  "passes, so every later rejection is uninterpretable. Fix "
                  "the instrument before evaluating anything else - do not "
                  "accept 'expect rejection, that's the gate working'."),
        )
        return state

    state.update(
        phase="calibration passed - report and package",
        proceed=True,
        next_command="python bench/report.py --out docs/run-sheet.md",
        note=("The instrument detects a real effect. Generated skills stay "
              "eval-only from here."),
    )
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="where the measurement is")
    ap.add_argument("command", choices=["status"], nargs="?", default="status")
    ap.add_argument("--runs-dir", default="runs")
    ap.add_argument("--n-tasks", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    s = assess(Path(args.runs_dir), args.n_tasks)
    if args.json:
        print(json.dumps(s, indent=2))
    else:
        print(f"phase      {s['phase']}")
        print(f"progress   {s['runs_completed']}/{s['of']} runs · "
              f"{s['passes_complete']}/{TOTAL_PASSES} passes complete")
        print(f"pass rate  {s['observed_pass_rate']:.1%} (observed so far)")
        print()
        print(s["note"])
        if s["next_command"]:
            print()
            print("next:")
            print(f"  {s['next_command']}")
    return 0 if s["proceed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
