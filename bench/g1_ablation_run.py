from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE_STEP = 2

DEPARTURES = {
    "autonomy.auto_review": False,
    "autonomy.approve_scoped_writes": True,
    "compaction_probe_step": PROBE_STEP,
    "active_local_tier": "local",
    "local_allowlist": ["compress_tool_output"],
    "compress_observations_over_tokens": 1200,
}

ARMS = [
    ("control", {}),
    ("no-compaction", {"compaction": False}),
    ("no-semantic-cache", {"semantic_cache": False}),
    ("no-prefix-stability", {"prefix_stability": False}),
    ("no-code-as-action", {"code_as_action": False}),
]


def write_configs(base: Path, out: Path) -> dict[str, Path]:
    import yaml

    raw = yaml.safe_load(base.read_text(encoding="utf-8"))
    raw["db_path"] = str((out / "agent.g1.db").resolve())
    raw["anchor_path"] = str((out / "anchor" / "chain.g1.anchor").resolve())

    raw.setdefault("autonomy", {})
    for key, value in DEPARTURES.items():
        section, _, leaf = key.rpartition(".")
        (raw.setdefault(section, {}) if section else raw)[leaf] = value

    paths = {}
    for arm, off in ARMS:
        cfg = dict(raw)
        cfg["mechanisms"] = {"compaction": True, "semantic_cache": True,
                             "prefix_stability": True, "code_as_action": True,
                             **off}
        p = out / f"config.{arm}.yaml"
        p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        paths[arm] = p
    return paths


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="G1 ablation arms, one per mechanism")
    p.add_argument("--suite", required=True, help="path to the task tree")
    p.add_argument("--base", default=str(ROOT / "config.local.yaml"))
    p.add_argument("--out", default=None, help="output directory")
    p.add_argument("--only", help="run one arm by name")
    args = p.parse_args(argv)

    out = Path(args.out) if args.out else ROOT / "runs" / "g1-ablation"
    (out / "anchor").mkdir(parents=True, exist_ok=True)
    configs = write_configs(Path(args.base), out)

    arms = [a for a, _ in ARMS if not args.only or a == args.only]
    summary = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "departures_from_config_local": DEPARTURES,
               "grader": "held out (open_measurement): pass/fail only reaches the model",
               "arms": {}}
    started = time.time()

    for arm in arms:
        dest = out / f"{arm}.json"
        print(f"\n=== {arm} ===", flush=True)
        t0 = time.time()
        r = subprocess.run(  # noqa: S603
            [sys.executable, str(ROOT / "bench" / "run.py"),
             "--config", str(configs[arm]), "--profile", "local",
             "arms", "--suite", args.suite, "--only", "skills-off",
             "--out", str(dest)],
            cwd=str(ROOT))
        elapsed = time.time() - t0
        row = {"exit": r.returncode, "wall_s": round(elapsed, 1),
               "out": str(dest)}
        if dest.exists():
            try:
                body = json.loads(dest.read_text(encoding="utf-8"))
                row["arms"] = {k: {"pass_rate": v.get("pass_rate"),
                                   "n": len(v.get("results", []))}
                               for k, v in body.get("arms", {}).items()}
            except (OSError, ValueError) as e:
                row["read_error"] = str(e)
        summary["arms"][arm] = row
        print(f"--- {arm}: exit {r.returncode}, {elapsed/60:.1f} min", flush=True)
        if arm == "control" and r.returncode != 0:
            print("control arm failed; stopping rather than measuring against "
                  "nothing", flush=True)
            break

    summary["total_min"] = round((time.time() - started) / 60, 1)
    (out / "summary.json").write_text(json.dumps(summary, indent=2),
                                      encoding="utf-8")
    print("\n" + json.dumps(summary, indent=2))
    print(f"\nwritten to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
