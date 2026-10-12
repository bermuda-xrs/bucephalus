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
B8 = "granite4.1:8b"
ARMS = {
    "control": {"profile": "full", "mechanisms": {k: False for k in NEW}},
    "small": {"profile": "small", "mechanisms": {}},
    "full8b": {"profile": "full", "mechanisms": {}, "model": B8, "window": 8192},
    "small8b": {"profile": "small", "mechanisms": {}, "model": B8, "window": 8192},
}
DEFAULT_ARMS = ("control", "small")
KV_BYTES_PER_TOKEN = 2 * 40 * 8 * 128 * 2
OVERHEAD_GB = 1.0
MARGIN_GB = 1.0


def write_config(base: Path, arm: str, out: Path) -> Path:
    import yaml

    raw = yaml.safe_load(base.read_text(encoding="utf-8"))
    raw.setdefault("autonomy", {}).update({"auto_review": False, "approve_scoped_writes": True})
    raw["mechanisms"] = {**raw.get("mechanisms", {}), **ARMS[arm]["mechanisms"]}
    raw["backends"]["cloud"]["tool_profile"] = ARMS[arm]["profile"]
    if "model" in ARMS[arm]:
        raw["backends"]["cloud"]["model"] = ARMS[arm]["model"]
        raw["backends"]["cloud"]["usable_context_window"] = ARMS[arm]["window"]
    p = out / f"config.{arm}.yaml"
    p.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return p


def model_gb(models: str, name: str) -> float | None:
    repo, _, tag = name.partition(":")
    f = Path(models) / "manifests" / "registry.ollama.ai" / "library" / repo / (tag or "latest")
    try:
        layers = json.loads(f.read_text(encoding="utf-8"))["layers"]
    except (OSError, ValueError, KeyError):
        return None
    return sum(x["size"] for x in layers if x.get("mediaType", "").endswith(".model")) / 2 ** 30


def needed_gb(weights_gb: float, ctx_tokens: int) -> float:
    return weights_gb + ctx_tokens * KV_BYTES_PER_TOKEN / 2 ** 30 + OVERHEAD_GB


def fits(weights_gb: float, ctx_tokens: int, available_gb: float) -> bool:
    return available_gb >= needed_gb(weights_gb, ctx_tokens) + MARGIN_GB


def preflight(arms, models: str, available_gb: float | None = None) -> dict:
    if available_gb is None:
        from agent import proc

        available_gb = proc.memory_pressure().get("available_gb") or 0.0
    out = {}
    for arm in arms:
        a = ARMS[arm]
        if "model" not in a:
            continue
        w = model_gb(models, a["model"])
        if w is None:
            out[arm] = {"model": a["model"], "fits": False, "why": "the model is not installed in " + models}
            continue
        need = needed_gb(w, a["window"])
        out[arm] = {"model": a["model"], "weights_gb": round(w, 2), "context": a["window"], "needed_gb": round(need, 2),
                    "margin_gb": MARGIN_GB, "available_gb": round(available_gb, 2), "fits": fits(w, a["window"], available_gb),
                    "estimate": "weights from the manifest; KV cache assumes 40 layers x 8 KV heads x 128 dim at fp16 "
                                "(unverified for this model); needed = weights + KV + 1 GB overhead"}
    return out


def prepare_data(data: Path, suite: str) -> None:
    import shutil

    p = Path(suite)
    files = sorted(p.glob("*.json")) if p.is_dir() else [p]
    for f in files:
        specs = json.loads(f.read_text(encoding="utf-8"))
        for spec in specs if isinstance(specs, list) else [specs]:
            g = spec.get("grader_dir")
            if g and (ROOT / g).is_dir():
                shutil.copytree(ROOT / g, data / g, dirs_exist_ok=True)


def run_arm(cfg: Path, arm: str, rep: int, suite: str, out: Path, models: str, timeout_s: int) -> Path:
    from agent.proc import kill_tree

    data = out / "data" / f"{arm}-{rep}"
    data.mkdir(parents=True, exist_ok=True)
    prepare_data(data, suite)
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


def summarise(out: Path, reps: int, arms=DEFAULT_ARMS) -> dict:
    rows, wall = [], {arm: [] for arm in arms}
    for rep in range(reps):
        got = {}
        for arm in arms:
            f = out / f"{arm}-{rep}.json"
            if f.exists():
                body = json.loads(f.read_text(encoding="utf-8"))
                got[arm] = {r["key"]: r for r in body["arms"]["skills-off"]["results"]}
        if "control" not in got or len(got) < 2:
            continue
        for key in got["control"]:
            if all(key in g for g in got.values()):
                rows.append({"rep": rep, "key": key, "passed": {a: g[key]["passed"] for a, g in got.items()},
                             "status": {a: g[key]["status"] for a, g in got.items()}})
                for a, g in got.items():
                    wall[a].append(g[key]["wall_s"])
    present = [a for a in arms if any(a in r["passed"] for r in rows)]
    vs = {}
    for a in present:
        if a == "control":
            continue
        b = sum(r["passed"]["control"] and not r["passed"][a] for r in rows)
        c = sum(r["passed"][a] and not r["passed"]["control"] for r in rows)
        vs[a] = {"only_control": b, "only_arm": c, "both": sum(r["passed"]["control"] and r["passed"][a] for r in rows),
                 "neither": sum(not r["passed"]["control"] and not r["passed"][a] for r in rows),
                 "mcnemar_exact_p": round(mcnemar_exact(b, c), 4)}
    from agent.provenance import code_identity

    return {
        "code": code_identity(), "n_pairs": len(rows),
        "arms": {a: {"passes": sum(r["passed"][a] for r in rows), "n": len(rows),
                     "mean_wall_s": round(sum(wall[a]) / len(wall[a]), 1) if wall[a] else None} for a in present},
        "vs_control": vs, "pairs": rows,
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
    p.add_argument("--arms", default=",".join(DEFAULT_ARMS), help="comma list from: " + ", ".join(ARMS))
    p.add_argument("--preflight", action="store_true", help="say whether the 8B arms fit in memory now, and stop")
    p.add_argument("--force", action="store_true", help="run the 8B arms even if the preflight says they do not fit")
    args = p.parse_args(argv)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in ARMS]
    if unknown or "control" not in arms:
        p.error(f"--arms must name known arms and include control; got {arms}, unknown {unknown}")
    models = os.environ.get("BUCEPHALUS_OLLAMA_MODELS") or str(ROOT.parent / "ollama-models")
    pre = preflight(arms, models)
    if pre:
        print(json.dumps(pre, indent=2), flush=True)
    if args.preflight:
        return 0 if all(v["fits"] for v in pre.values()) else 3
    if pre and not args.force and not all(v["fits"] for v in pre.values()):
        print("the 8B arms do not fit in memory now; free memory or pass --force. Nothing was started.", flush=True)
        return 3

    out = Path(args.out) if args.out else ROOT / "runs" / f"profile-{time.strftime('%Y%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    cfgs = {arm: write_config(Path(args.base), arm, out) for arm in arms}
    os.environ["BUCEPHALUS_DATA_DIR"] = str(out / "data" / "runner")
    os.environ["BUCEPHALUS_CONFIG_DIR"] = str(out / "data" / "runner" / "config")
    cfg = load_config(args.base)
    endpoint = cfg.backends["cloud"].endpoint
    os.environ["BUCEPHALUS_OLLAMA_MODELS"] = models
    try:
        local.start_server(endpoint, ROOT)
        print("model loaded:", local.run(endpoint, cfg.backends["cloud"].model, ROOT), flush=True)
        for rep in range(args.reps):
            order = list(arms)
            random.Random(args.seed + rep).shuffle(order)
            for arm in order:
                t0 = time.time()
                run_arm(cfgs[arm], arm, rep, args.suite, out, models, args.timeout_min * 60)
                print(f"rep {rep} {arm}: {(time.time() - t0) / 60:.1f} min", flush=True)
    finally:
        local.stop_owned()
    summary = summarise(out, args.reps, arms)
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
