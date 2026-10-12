from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

NEW = ("diagnostics", "verify_before_stop", "constrained_calls", "stuck_detection", "shadow_checkpoints", "agents_md")
ARMS = {
    "control": {"profile": "full", "mechanisms": {k: False for k in NEW}},
    "small": {"profile": "small", "mechanisms": {}},
}


def write_config(base: Path, arm: str, out: Path) -> Path:
    import yaml

    raw = yaml.safe_load(base.read_text(encoding="utf-8"))
    raw.setdefault("autonomy", {}).update({"auto_review": False, "approve_scoped_writes": True})
    raw["mechanisms"] = {**raw.get("mechanisms", {}), **ARMS[arm]["mechanisms"]}
    raw["backends"]["cloud"]["tool_profile"] = ARMS[arm]["profile"]
    p = out / f"config.{arm}.yaml"
    p.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return p


def run_arm(cfg: Path, arm: str, rep: int, suite: str, out: Path, models: str, timeout_s: int) -> Path:
    from agent.proc import kill_tree

    data = out / "data" / f"{arm}-{rep}"
    data.mkdir(parents=True, exist_ok=True)
    dest = out / f"{arm}-{rep}.json"
    env = {**os.environ, "BUCEPHALUS_DATA_DIR": str(data), "BUCEPHALUS_CONFIG_DIR": str(data / "config"),
           "BUCEPHALUS_OLLAMA_MODELS": models, "PYTHONUTF8": "1"}
    p = subprocess.Popen(  # noqa: S603
        [sys.executable, str(ROOT / "bench" / "run.py"), "--config", str(cfg), "--profile", "local",
         "arms", "--suite", suite, "--only", "skills-off", "--out", str(dest)], cwd=str(ROOT), env=env)
    try:
        p.wait(timeout_s)
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        print(f"{arm}-{rep}: timed out", flush=True)
    return dest


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def summarise(out: Path, reps: int) -> dict:
    rows, wall = [], {"control": [], "small": []}
    for rep in range(reps):
        got = {}
        for arm in ARMS:
            f = out / f"{arm}-{rep}.json"
            if f.exists():
                body = json.loads(f.read_text(encoding="utf-8"))
                got[arm] = {r["key"]: r for r in body["arms"]["skills-off"]["results"]}
        if len(got) < 2:
            continue
        for key in got["control"]:
            if key in got["small"]:
                c, s = got["control"][key], got["small"][key]
                rows.append({"rep": rep, "key": key, "control": c["passed"], "small": s["passed"],
                             "control_status": c["status"], "small_status": s["status"]})
                wall["control"].append(c["wall_s"])
                wall["small"].append(s["wall_s"])
    b = sum(r["control"] and not r["small"] for r in rows)
    c = sum(r["small"] and not r["control"] for r in rows)
    return {
        "n_pairs": len(rows), "control_passes": sum(r["control"] for r in rows),
        "small_passes": sum(r["small"] for r in rows), "only_control": b, "only_small": c,
        "both": sum(r["control"] and r["small"] for r in rows),
        "neither": sum(not r["control"] and not r["small"] for r in rows),
        "mcnemar_exact_p": round(mcnemar_exact(b, c), 4),
        "mean_wall_s": {k: round(sum(v) / len(v), 1) if v else None for k, v in wall.items()},
        "pairs": rows,
        "read_as": "a sighting, not a result: greedy decoding makes repetitions of one task near-copies, "
                   "so the effective n is closer to the number of distinct tasks than to n_pairs",
    }


def _main(argv=None) -> int:
    from agent.config import load_config
    from agent.models import local

    p = argparse.ArgumentParser(description="control profile vs small profile, paired by (task, repetition)")
    p.add_argument("--suite", required=True)
    p.add_argument("--base", default=str(ROOT / "config.local.yaml"))
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", default=None)
    p.add_argument("--timeout-min", type=int, default=45)
    args = p.parse_args(argv)

    out = Path(args.out) if args.out else ROOT / "runs" / f"profile-{time.strftime('%Y%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    cfgs = {arm: write_config(Path(args.base), arm, out) for arm in ARMS}
    os.environ["BUCEPHALUS_DATA_DIR"] = str(out / "data" / "runner")
    os.environ["BUCEPHALUS_CONFIG_DIR"] = str(out / "data" / "runner" / "config")
    cfg = load_config(args.base)
    endpoint = cfg.backends["cloud"].endpoint
    models = os.environ.get("BUCEPHALUS_OLLAMA_MODELS") or str(ROOT.parent / "ollama-models")

    os.environ["BUCEPHALUS_OLLAMA_MODELS"] = models
    try:
        local.start_server(endpoint, ROOT)
        print("model loaded:", local.run(endpoint, cfg.backends["cloud"].model, ROOT), flush=True)
        for rep in range(args.reps):
            order = list(ARMS)
            random.Random(args.seed + rep).shuffle(order)
            for arm in order:
                t0 = time.time()
                run_arm(cfgs[arm], arm, rep, args.suite, out, models, args.timeout_min * 60)
                print(f"rep {rep} {arm}: {(time.time() - t0) / 60:.1f} min", flush=True)
    finally:
        local.stop_owned()
    summary = summarise(out, args.reps)
    (out / "paired.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "pairs"}, indent=2))
    return 0


def main(argv=None) -> int:
    import sys
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'src'))
    from agent.awake import keep_awake

    with keep_awake():
        return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
