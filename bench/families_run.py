from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# every mechanism declared off: the small profile's defaults (on for the kept items) must not leak into the base
BASE = {"backend.tool_profile": "small", "backend.reasoning_field": False, "backend.carry_field": False,
        "backend.compact_prompt": False, "backend.evidence_pins": False, "backend.stuck_patterns": False,
        "backend.escape_check": False, "backend.edit_format": "default", "backend.context_fill_ratio": 0.28,
        "backend.edit_first": False, "backend.write_diff": False, "backend.repeat_note": False}


def _arm(families, **over):
    return {"overrides": {**BASE, **{f"backend.{k}": v for k, v in over.items()}}, "families": families}


ARMS: dict[str, dict] = {
    "base": {"overrides": BASE, "families": None},
    "L14": _arm(["write_function", "needle"], send_tool_list=True),
    "B1": _arm(["write_function", "multipage"], reasoning_field=True),
    "B2": _arm(["multipage", "needle"], carry_field=True),
    "B3": _arm(["write_function", "config_edit"], code_out_of_json=True),
    "B4": _arm(["write_function", "needle"], tool_call_mode="native"),
    "B5": _arm(["write_function", "arithmetic"], tool_names="familiar"),
    "B6": _arm(["needle", "extract_json"], compact_prompt=True),
    "C1": _arm(["multipage", "needle"], evidence_pins=True),
    "C2": _arm(["multipage", "config_edit"], progress_file=True),
    "C3": _arm(["multipage", "needle"], context_fill_ratio=0.5),
    "D1": _arm(["write_function", "unanswerable"], evaluator_pass=True),
    "D2": _arm(["multi_file", "arithmetic"], stuck_patterns=True),
    "D3": _arm(["write_function", "config_edit"], escape_check=True),
    "E1": _arm(["config_edit", "write_function"], edit_format="hashline"),
    "F1": _arm(["config_edit", "write_function"], edit_first=True),
    "F2": _arm(["arithmetic", "extract_json"], compute_first=True),
}


def _together(*names):
    over = dict(BASE)
    for n in names:
        over.update({k: v for k, v in ARMS[n]["overrides"].items() if BASE.get(k) != v})
    return {"overrides": over, "families": None}


# items measured one at a time, run together on every family: do they add up; each kept-X leaves X out
KEPT = ("B2", "B6", "C1", "C3", "D2", "D3", "E1", "F1")
COMBINED = {"kept": _together(*KEPT), **{f"kept-{x}": _together(*(k for k in KEPT if k != x)) for x in KEPT}}
# candidates on top of kept, which is what the small profile now runs
ON_KEPT = {"H1": {"overrides": {**COMBINED["kept"]["overrides"], "backend.write_diff": True},
                  "families": ["config_edit", "write_function"]},
           "H2": {"overrides": {**COMBINED["kept"]["overrides"], "backend.repeat_note": True},
                  "families": ["unanswerable", "arithmetic"]}}
ALL_ARMS = {**ARMS, **COMBINED, **ON_KEPT}


def arm_raw(raw: dict, arm: str) -> dict:
    from agent import evals

    return evals.apply_overrides(raw, ALL_ARMS[arm]["overrides"])


def runs_on(arm: str, spec: dict) -> bool:
    fams = ALL_ARMS[arm]["families"]
    return fams is None or spec["family"] in fams


def load_done(path: Path) -> dict:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["key"]: r for r in rows}


def _main(argv=None) -> int:
    import yaml

    from agent import evals, families
    from agent.models import local
    from agent.runlock import RunLock, RunLockBusy, measurement_lock_path

    p = argparse.ArgumentParser(description="paired arms over generated task families, one run per distinct instance")
    p.add_argument("--families", default=",".join(families.FAMILIES))
    p.add_argument("--split", default="heldout", choices=("dev", "heldout"))
    p.add_argument("--n", type=int, default=10, help="instances per family")
    p.add_argument("--skip", type=int, default=0, help="instances per family already measured, taken from the front")
    p.add_argument("--arms", default="base")
    p.add_argument("--base", default=str(ROOT / "config.local.yaml"))
    p.add_argument("--model", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args(argv)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    if "base" not in arms or any(a not in ALL_ARMS for a in arms):
        p.error(f"--arms must include base and name known arms: {sorted(ALL_ARMS)}")
    fams = [f.strip() for f in args.families.split(",") if f.strip()]
    out = Path(args.out) if args.out else ROOT / "runs" / f"families-{time.strftime('%Y%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("BUCEPHALUS_DATA_DIR", str(out / "data" / "runner"))
    os.environ.setdefault("BUCEPHALUS_CONFIG_DIR", str(out / "data" / "runner" / "config"))
    raw = evals.measurement_raw(yaml.safe_load(Path(args.base).read_text(encoding="utf-8")), model=args.model)
    specs = families.instances(fams, args.split, args.n, args.skip)
    try:
        lock = RunLock(measurement_lock_path(ROOT), "measurement").acquire()
    except RunLockBusy as e:
        print(f"REFUSING TO START.\n{e}")
        return 2
    endpoint = raw["backends"]["cloud"].get("endpoint")
    try:
        local.start_server(endpoint, ROOT)
        print("model loaded:", local.run(endpoint, raw["backends"]["cloud"]["model"], ROOT), flush=True)
        cards = {a: evals.card_for(arm_raw(raw, a), out / "cards" / a) for a in arms}
        (out / "cards.json").write_text(json.dumps(cards, indent=2, default=str), encoding="utf-8")
        done = {a: load_done(out / f"{a}.jsonl") for a in arms}
        for i, spec in enumerate(specs):
            order = list(arms)
            random.Random(args.seed + i).shuffle(order)
            for a in order:
                if spec["key"] in done[a] or not runs_on(a, spec):
                    continue
                r = evals.run_instance(arm_raw(raw, a), spec, out / "data" / a / spec["key"])
                done[a][spec["key"]] = r
                with (out / f"{a}.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(r) + "\n")
                print(f"[{i + 1}/{len(specs)}] {a} {spec['key']}: {r['status']} ({r['wall_s']} s)", flush=True)
    finally:
        local.stop_owned()
        lock.release()
    summary = evals.summarise({a: list(done[a].values()) for a in arms}, "base")
    summary.update(split=args.split, per_family=args.n, skip=args.skip, families=summary["families"],
                   arms_defined={a: ALL_ARMS[a] for a in arms},
                   family_version=families.FAMILY_VERSION, card=cards)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("n", "n_families", "arms", "vs_base")}, indent=2))
    return 0


def main(argv=None) -> int:
    from agent.awake import keep_awake

    with keep_awake():
        return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
