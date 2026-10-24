from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from measurement import open_measurement  # noqa: E402
from agent.execute.loop import run_task  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.models.base import BackendError  # noqa: E402
from agent.models.ollama import ModelStall, reset_rates  # noqa: E402
from agent import HARNESS_VERSION, TOOLSET_VERSION  # noqa: E402
from agent.proc import (  # noqa: E402
    free_memory_gb, memory_pressure, reap_orphans, worker_private_gb,
    worker_rss_gb)

COMMIT_RATIO_SUSPECT = 1.40
EMERGENCY_FREE_GB = 0.50

ABORT_EXCLUDED_MAX = 3

WALL_OUTLIER_FACTOR = 3.0

WALL_OUTLIER_ABS_S = 900.0

CACHE_DROP_FRACTION = 0.5
MIN_CACHE_SAMPLES = 3

REFRESH_COMMIT_RATIO = 1.35
from agent.runlock import RunLock, RunLockBusy  # noqa: E402
from agent.workspace import reset_workspace  # noqa: E402

TARGET_BAND = (0.35, 0.65)

SKILLS_FOR_ARM: list = []
BUDGET_SCALE: float = 1.0


def provision(ctx, spec: dict) -> None:
    reset_workspace(ctx.config.root, spec.get("artifact_scope") or [],
                    spec.get("fixtures") or {})


def run_once(ctx, spec: dict, run_ix: int) -> dict:
    provision(ctx, spec)
    contract = TaskContract(
        goal=spec["goal"],
        success_criterion=spec.get("success_criterion", "verify_program"),
        grader_dir=spec.get("grader_dir"),
        output_schema=spec.get("output_schema", {}),
        artifact_scope=spec.get("artifact_scope", []),
    )
    budget = Budget(
        usd=spec.get("budget_usd", 0.0) or 0.0,
        seconds=int((spec.get("budget_seconds")
                     or ctx.config.limits.default_task_budget_seconds)
                    * BUDGET_SCALE),
    )
    task = create_task(ctx.conn, ctx.log, contract, budget)
    ctx.set_frozen_skills(task.id, SKILLS_FOR_ARM)
    press_before = memory_pressure()
    mem_before = press_before.get("available_gb", free_memory_gb())
    rss_before = worker_rss_gb()
    t0 = time.time()
    result = run_task(ctx, task.id)
    wall = round(time.time() - t0, 1)
    press_after = memory_pressure()
    mem_after = press_after.get("available_gb", free_memory_gb())
    m = ctx.telemetry.metrics(task.id)
    low = min([g for g in (mem_before, mem_after) if g is not None], default=None)
    ratios = [p["commit_ratio"] for p in (press_before, press_after)
              if p.get("commit_ratio") is not None]
    worst_ratio = max(ratios) if ratios else None
    ratio_high = bool(worst_ratio is not None
                      and worst_ratio >= COMMIT_RATIO_SUSPECT)
    suspect = bool(low is not None and low < EMERGENCY_FREE_GB)
    return {
        "run": run_ix,
        "task_id": task.id,
        "status": result.status,
        "passed": result.status in ("done", "done_unannounced"),
        "wall_s": wall,
        "free_gb_before": None if mem_before is None else round(mem_before, 2),
        "free_gb_after": None if mem_after is None else round(mem_after, 2),
        "memory_suspect": suspect,
        "commit_ratio_high": ratio_high,
        "commit_ratio": worst_ratio,
        "worker_rss_before_gb": rss_before,
        "worker_rss_after_gb": worker_rss_gb(),
        "worker_private_after_gb": worker_private_gb(),
        "free_tokens": m.free_tokens,
        "cost_usd": m.cost_usd,
        "n_calls": m.n_calls,
        "n_unreturned": m.n_unreturned,
        "cache_hit_ratio": m.cache_hit_ratio,
    }


def refresh_worker(ctx) -> dict:
    b = ctx.config.backends["cloud"]
    url = _ollama_admin_base(b) + "/api/generate"
    t0 = time.time()
    before = worker_rss_gb()
    try:
        _ollama_post(url, {"model": b.model, "keep_alive": 0})
        time.sleep(3.0)
        _ollama_post(url, {"model": b.model, "prompt": "ok", "stream": False,
                           "keep_alive": "30m",
                           "options": {"num_ctx": b.usable_context_window,
                                       "num_predict": 1}})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e),
                "cost_s": round(time.time() - t0, 1),
                "rss_before_gb": before, "rss_after_gb": worker_rss_gb()}
    return {"ok": True, "cost_s": round(time.time() - t0, 1),
            "rss_before_gb": before, "rss_after_gb": worker_rss_gb()}


def _ollama_admin_base(b) -> str:
    url = (b.base_url or b.endpoint or "").rstrip("/")
    return url[:-3].rstrip("/") if url.endswith("/v1") else url


def _ollama_loaded(b) -> dict | None:
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(
                _ollama_admin_base(b) + "/api/ps", timeout=5.0) as r:
            models = (_json.loads(r.read().decode("utf-8")) or {}).get("models") or []
    except Exception:  # noqa: BLE001
        return None
    for m in models:
        if m.get("name") == b.model or m.get("model") == b.model:
            return m
    return {}


def _ollama_post(url: str, body: dict) -> None:
    import urllib.request

    req = urllib.request.Request(
        url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=600).read()


def run_with_backend_retry(ctx, spec: dict, run_ix: int, key: str) -> dict:
    stalls: list[dict] = []
    for stall_attempt in (1, 2):
        try:
            out = run_once(ctx, spec, run_ix)
            if stalls:
                out["machine_fault"] = True
                out["stalls"] = stalls
                print(f"    {key}: recovered after {len(stalls)} stall(s)",
                      flush=True)
            return out
        except ModelStall as e:
            stalls.append({"attempt": stall_attempt,
                           "observed_tok_s": round(e.observed_rate, 3),
                           "median_tok_s": round(e.median_rate, 3),
                           "tokens": e.tokens, "elapsed_s": round(e.elapsed, 1)})
            print(f"    {key}: MACHINE FAULT - {e}", flush=True)
            if stall_attempt == 1:
                print(f"    {key}: re-running once", flush=True)
                continue
            print(f"    {key}: stalled twice, EXCLUDED (not a failure)",
                  flush=True)
            return {
                "run": run_ix, "task_id": None, "status": "excluded_machine_fault",
                "passed": False, "unmeasured": True, "excluded": True,
                "machine_fault": True, "stalls": stalls, "wall_s": 0.0,
                "free_gb_before": None, "free_gb_after": None,
                "memory_suspect": False, "commit_ratio": None,
                "commit_ratio_high": False, "wall_outlier": False,
                "worker_rss_before_gb": None, "worker_rss_after_gb": None,
                "free_tokens": 0, "cost_usd": 0.0,
                "cache_hit_ratio": None,
            }
        except BackendError as e:
            print(f"    {key}: backend error, retrying once - {e}", flush=True)
            time.sleep(10)
            break

    try:
        return run_once(ctx, spec, run_ix)
    except BackendError as e:
        print(f"    {key}: backend error again, recording as UNMEASURED",
              flush=True)
        return {
            "run": run_ix, "task_id": None, "status": "backend_error",
            "passed": False, "unmeasured": True, "wall_s": 0.0,
            "free_gb_before": None, "free_gb_after": None,
            "memory_suspect": False, "commit_ratio": None,
            "commit_ratio_high": False, "wall_outlier": False,
            "worker_rss_before_gb": None, "worker_rss_after_gb": None,
            "free_tokens": 0, "cost_usd": 0.0, "error": str(e),
            "cache_hit_ratio": None,
        }


def assert_machine_fault_never_scored_as_failure(row: dict) -> None:
    if row.get("excluded") and not row.get("unmeasured"):
        raise AssertionError(
            f"{row.get('key')}: excluded_machine_fault must be unmeasured, "
            "or a machine event depresses the pass rate")
    status = str(row.get("status") or "")
    if status == "crashed":
        raise AssertionError(
            f"{row.get('key')}: status `crashed`, which the suite scores as "
            "FAILED. Machine events have their own terminal statuses, so a "
            "crash means a path was missed. Abort and diagnose rather than "
            f"scoring it. stalls={row.get('stalls')} error={row.get('error')}")
    if row.get("machine_fault") and row.get("passed") and row.get("excluded"):
        raise AssertionError(
            f"{row.get('key')}: excluded tasks cannot also be passes")


def _library_provenance() -> list[str]:
    try:
        from agent.execute.libraries import REGISTRY
        return REGISTRY.provenance()
    except Exception:  # noqa: BLE001
        return []


def classify(passes: list[bool]) -> str:
    if all(passes):
        return "stable_pass"
    if not any(passes):
        return "stable_fail"
    return "flaky"


def ledger_path(out: str | Path) -> Path:
    p = Path(out)
    return p.with_suffix(p.suffix + ".partial.jsonl")


def _isolation_state(ctx) -> dict:
    from agent.kernel.scope import isolation_state
    return isolation_state(ctx)


def _median_or_none(values: list) -> float | None:
    return round(statistics.median(values), 4) if values else None


def run_signature(args, n_specs: int) -> dict:
    return {"suite": args.suite, "split": args.split,
            "executor": args.executor, "order_seed": args.order_seed,
            "limit": args.limit, "tier": sorted(args.tier) if args.tier else None,
            "only": sorted(args.only) if args.only else None,
            "skill": args.skill,
            "profile": args.profile, "n_specs": n_specs}


def append_run(path: Path, sig: dict, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    first = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if first:
            fh.write(json.dumps({"_signature": sig}) + "\n")
        fh.write(json.dumps(record) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_completed(path: Path, sig: dict) -> dict[tuple[int, str], dict]:
    if not path.exists():
        return {}
    done: dict[tuple[int, str], dict] = {}
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            print(f"  ledger: dropping truncated line {i + 1}")
            continue
        if "_signature" in row:
            if row["_signature"] != sig:
                raise SystemExit(
                    f"{path} was written by a different run configuration:\n"
                    f"  ledger: {row['_signature']}\n  now:    {sig}\n"
                    "Refusing to blend two measurements. Delete it or change --out."
                )
            continue
        done[(row["run"], row["key"])] = row
    return done


def write_report(dest: Path, report: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(tmp, dest)


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="measure the suite's noise floor")
    ap.add_argument("--config", default="config.local.yaml")
    ap.add_argument("--profile", default="local",
                    help="enforced backend profile; 'local' forbids cloud backends")
    ap.add_argument("--suite", required=True,
                    help="suite name to measure, e.g. a suite loaded into the "
                         "user data store. REQUIRED - there is no default")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-flaky", type=int, default=8)
    ap.add_argument("--split", choices=["drafting", "eval"])
    ap.add_argument("--limit", type=int,
                    help="first N tasks in SUITE ORDER, taken before the "
                         "per-run shuffle (D9). For a smoke check")
    ap.add_argument("--only", action="append", metavar="KEY",
                    help="run exactly these task keys; repeatable. The precise "
                         "form of a sighting shot: a task can only become a "
                         "discordant pair if the skill addresses its trap, so "
                         "spending turns on tasks it says nothing about buys "
                         "no information")
    ap.add_argument("--tier", choices=["easy", "medium", "hard"], action="append",
                    help="restrict to these tiers; repeatable. --limit alone "
                         "takes the FIRST N tasks, which are all easy, so a "
                         "sighting shot aimed at where the signal is in doubt "
                         "needs this instead")
    ap.add_argument("--out", default="runs/noise-floor.json")
    ap.add_argument("--auto-review", action="store_true",
                    help="keep the LLM deputy in the loop (adds variance)")
    ap.add_argument("--budget-scale", type=float, default=1.0,
                    help="multiply every task's TIME budget. Use it when the "
                         "executor is slower than the one the budgets were "
                         "written for, so the comparison is between models "
                         "rather than between step budgets. Recorded in the "
                         "report.")
    ap.add_argument("--executor",
                    help="override the executor model, making this a MODEL "
                         "arm rather than a skill arm (e.g. granite4.1:8b)")
    ap.add_argument("--skill", help="run WITH this skill (name or id) instead of bare")
    ap.add_argument("--order-seed", type=int, default=None,
                    help="seed for per-run task order. Default: random, and "
                         "recorded. Order is randomised because suite order "
                         "is fixed (E..M..H..) and any drift across a long "
                         "run is therefore confounded with TIER - hard tasks "
                         "always ran last, under the worst conditions.")
    ap.add_argument("--resume", action="store_true",
                    help="reuse completed runs from the .partial.jsonl ledger "
                         "beside --out; a killed pass restarts where it stopped")
    args = ap.parse_args(argv)

    if args.order_seed is None:
        args.order_seed = random.randrange(1, 2**31 - 1)
        print(f"task order seed (generated): {args.order_seed}")
    else:
        print(f"task order seed (given): {args.order_seed}")

    from agent.runlock import measurement_lock_path

    try:
        lock = RunLock(measurement_lock_path(Path(args.config).resolve().parent),
                       "noise floor").acquire()
    except RunLockBusy as e:
        print(f"REFUSING TO START.\n{e}")
        return 2
    try:
        return _noise_floor(args)
    finally:
        lock.release()


def _noise_floor(args) -> int:
    before_gb = free_memory_gb()
    reaped = reap_orphans()
    free_gb = free_memory_gb()
    if reaped:
        print(f"reaped {len(reaped)} orphaned worker(s): {reaped}")

    ctx = open_measurement(args.config, profile=args.profile, isolate=True,
                           why="paired trials and noise floor: arms and runs "
                               "must be independent observations")
    exec_cfg = ctx.config.backends["cloud"]
    press = memory_pressure()
    if press:
        print(f"memory at start: {press.get('available_gb')} GB available, "
              f"commit ratio {press.get('commit_ratio')} "
              f"(suspect >= {COMMIT_RATIO_SUSPECT}, "
              f"refresh >= {REFRESH_COMMIT_RATIO})")
        rss0 = worker_rss_gb()
        if rss0 is not None:
            print(f"model worker resident at start: {rss0} GB")

        loaded = _ollama_loaded(exec_cfg)
        warm_at_start = None if loaded is None else bool(loaded)
        server_ctx = (loaded or {}).get("context_length")
        print(f"model warm at start: {warm_at_start}"
              f"  keep_alive={exec_cfg.keep_alive!r}"
              f"  configured ctx={exec_cfg.budgeted_window}"
              f"  server ctx={server_ctx}")
        if server_ctx is not None and server_ctx < exec_cfg.budgeted_window:
            print(f"WARNING: the server allocated {server_ctx} tokens of "
                  f"context but this config budgets against "
                  f"{exec_cfg.budgeted_window}. M3.4's ceiling is computed "
                  f"from a number the server will not honour, and Ollama "
                  f"truncates silently. Start the server with "
                  f"OLLAMA_CONTEXT_LENGTH={exec_cfg.budgeted_window}.")
        if (press.get("commit_ratio") or 0) >= COMMIT_RATIO_SUSPECT:
            print("WARNING: already at or above the suspect commit ratio "
                  "before the first task. Paging rewrites a seconds-based "
                  "measurement - one call has been seen 18x slower.")

    if args.executor:
        from agent.models.registry import build_backends

        ctx.config.backends["cloud"].model = args.executor
        ctx.backends = build_backends(ctx.config)
        reset_rates()
        ctx.config.profile_provenance["executor_override"] = args.executor
        for name, b in ctx.config.backends.items():
            ctx.config.profile_provenance.setdefault("backends", {})[name] = {
                "kind": b.kind, "model": b.model, "tier": b.trust_tier}
        print(f"executor overridden -> {args.executor}")

    global SKILLS_FOR_ARM, BUDGET_SCALE
    BUDGET_SCALE = args.budget_scale
    if args.budget_scale != 1.0:
        print(f"time budgets scaled x{args.budget_scale}")
    if args.skill:
        row = ctx.conn.execute(
            "SELECT id FROM skills WHERE id=? OR name=? ORDER BY version DESC LIMIT 1",
            (args.skill, args.skill),
        ).fetchone()
        if not row:
            print(f"no skill named {args.skill!r}")
            return 2
        SKILLS_FOR_ARM = [ctx.skills.load(row[0])]
        print(f"arm: WITH skill {args.skill!r}")
    else:
        print("arm: baseline (no skills)")

    if not args.auto_review:
        ctx.config.autonomy.auto_review = False
        ctx.config.autonomy.approve_scoped_writes = True
        print("auto-review OFF for measurement (code gate still enforced: "
              "scope, taint ceilings, protected paths, trusted-tier parking). "
              "Pass --auto-review to include the deputy.")
    suite = ctx.userdata.get_suite_by_name(args.suite)
    if suite is None:
        print(f"no suite named {args.suite!r}. Load it first:\n"
              f"  python bench/suites/archive/aero_calc.py --load --config {args.config}")
        return 2

    specs = suite.to_specs()
    if args.split:
        specs = [s for s in specs
                 if (s.get("output_schema") or {}).get("x-split") == args.split]
    if args.only:
        wanted = set(args.only)
        specs = [s for s in specs if s["key"] in wanted]
        missing = wanted - {s["key"] for s in specs}
        if missing:
            print(f"no such task(s): {', '.join(sorted(missing))}")
            return 2
    if args.tier:
        specs = [s for s in specs
                 if (s.get("output_schema") or {}).get("x-tier") in set(args.tier)]
    if args.limit:
        specs = specs[: args.limit]
    if not specs:
        print("no tasks matched")
        return 2

    print(f"noise floor · {len(specs)} tasks x {args.runs} runs = "
          f"{len(specs) * args.runs} runs · profile={ctx.config.profile}")
    print(f"backends: {ctx.config.profile_provenance.get('backends')}\n")

    dest = Path(ctx.config.resolve(args.out))
    ledger = ledger_path(dest)
    sig = run_signature(args, len(specs))
    done = load_completed(ledger, sig) if args.resume else {}
    if args.resume:
        print(f"resuming: {len(done)} of {len(specs) * args.runs} runs already "
              f"in {ledger.name}")
    elif ledger.exists():
        raise SystemExit(
            f"{ledger} already exists. Pass --resume to continue that pass, "
            "or delete it / choose another --out to start fresh."
        )

    results: dict[str, list[dict]] = {}
    started = time.time()
    reloads: list[dict] = []
    for r in range(args.runs):
        ordered = list(specs)
        random.Random(f"{args.order_seed}:{r}").shuffle(ordered)
        print(f"  task order for run {r + 1}: "
              f"{', '.join(x['key'] for x in ordered)}")
        for i, spec in enumerate(ordered, 1):
            key = spec["key"]
            cached = done.get((r, key))
            if cached is not None:
                results.setdefault(key, []).append(cached)
                print(f"  run {r + 1}/{args.runs}  {i:>2}/{len(specs)}  {key:<6} "
                      f"{cached['status']:<16} {cached['wall_s']:>6.1f}s  (resumed)",
                      flush=True)
                continue
            pre = memory_pressure()
            if (pre.get("commit_ratio") or 0) >= REFRESH_COMMIT_RATIO:
                info = refresh_worker(ctx)
                info.update({"before_task": key, "run": r,
                             "trigger_ratio": pre.get("commit_ratio")})
                after = memory_pressure()
                info["ratio_after"] = after.get("commit_ratio")
                reloads.append(info)
                print(f"    worker refresh before {key}: "
                      f"ratio {info['trigger_ratio']} -> {info['ratio_after']}, "
                      f"RSS {info['rss_before_gb']} -> {info['rss_after_gb']} GB, "
                      f"cost {info['cost_s']}s"
                      + ("" if info["ok"] else f"  FAILED: {info.get('error')}"),
                      flush=True)

            out = run_with_backend_retry(ctx, spec, r, key)
            out["key"] = key
            assert_machine_fault_never_scored_as_failure(out)

            excluded_so_far = sum(
                1 for xs in results.values() for x in xs if x.get("excluded")
            ) + (1 if out.get("excluded") else 0)
            if excluded_so_far > ABORT_EXCLUDED_MAX:
                append_run(ledger, sig, out)
                raise SystemExit(
                    f"ABORT: {excluded_so_far} tasks excluded as machine "
                    f"faults, above the limit of {ABORT_EXCLUDED_MAX}. Too "
                    "much data is missing for this to be a measurement. The "
                    "ledger is intact and resumable; partial results must NOT "
                    "be reported as a pass rate.")

            prior = [x["wall_s"] for xs in results.values() for x in xs
                     if not x.get("unmeasured")]
            if len(prior) >= 3:
                med = statistics.median(prior)
                out["wall_outlier"] = bool(med > 0 and
                                           out["wall_s"] > WALL_OUTLIER_FACTOR * med)
                out["wall_vs_median"] = round(out["wall_s"] / med, 2) if med else None
            else:
                out["wall_outlier"] = out["wall_s"] > WALL_OUTLIER_ABS_S
                out["wall_vs_median"] = None
            if out["wall_outlier"]:
                out["memory_suspect"] = True

            seen = [x["cache_hit_ratio"] for xs in results.values() for x in xs
                    if x.get("cache_hit_ratio") is not None
                    and not x.get("unmeasured")]
            mine = out.get("cache_hit_ratio")
            if mine is not None and len(seen) >= MIN_CACHE_SAMPLES:
                cmed = statistics.median(seen)
                out["cache_drop"] = bool(cmed > 0
                                         and mine < CACHE_DROP_FRACTION * cmed)
                out["cache_vs_median"] = round(mine / cmed, 2) if cmed else None
            else:
                out["cache_drop"] = False
                out["cache_vs_median"] = None
            out["reload_before_this_task"] = bool(
                reloads and reloads[-1]["before_task"] == key
                and reloads[-1]["run"] == r)
            results.setdefault(key, []).append(out)
            append_run(ledger, sig, out)
            flag = ""
            if out.get("cache_drop"):
                flag = (f"  !! CACHE RATIO {out.get('cache_hit_ratio'):.2f} at "
                        f"{out.get('cache_vs_median')}x the run median "
                        f"- the stable prefix may be mutating")
            elif out.get("wall_outlier"):
                flag = (f"  !! WALL OUTLIER {out.get('wall_vs_median')}x median "
                        f"- wall-clock suspect")
            elif out.get("memory_suspect"):
                flag = (f"  !! FREE MEMORY {out.get('free_gb_after')} GB below "
                        f"{EMERGENCY_FREE_GB} - wall-clock suspect")
            elif out.get("commit_ratio_high"):
                flag = f"  (commit {out.get('commit_ratio')}, recorded not flagged)"
            print(f"  run {r + 1}/{args.runs}  {i:>2}/{len(specs)}  {key:<6} "
                  f"{out['status']:<16} {out['wall_s']:>6.1f}s{flag}", flush=True)

    rows = []
    unmeasured_keys = []
    for key, runs in results.items():
        measured = [x for x in runs if not x.get("unmeasured")]
        if len(measured) != len(runs):
            unmeasured_keys.append(key)
        if not measured:
            rows.append({
                "key": key,
                "tier": (next(s for s in specs if s["key"] == key)
                         .get("output_schema") or {}).get("x-tier", "?"),
                "split": (next(s for s in specs if s["key"] == key)
                          .get("output_schema") or {}).get("x-split", "?"),
                "classification": "unmeasured", "passes": 0, "runs": 0,
                "mean_wall_s": 0.0, "memory_suspect": False,
                "wall_outlier": False, "commit_ratio_high": False,
                "min_free_gb": None,
                "statuses": [x["status"] for x in runs],
            })
            continue
        runs = measured
        passes = [x["passed"] for x in runs]
        spec = next(s for s in specs if s["key"] == key)
        rows.append({
            "key": key,
            "tier": (spec.get("output_schema") or {}).get("x-tier", "?"),
            "split": (spec.get("output_schema") or {}).get("x-split", "?"),
            "classification": classify(passes),
            "passes": sum(passes), "runs": len(passes),
            "mean_wall_s": round(statistics.mean(x["wall_s"] for x in runs), 1),
            "memory_suspect": any(x.get("memory_suspect") for x in runs),
            "wall_outlier": any(x.get("wall_outlier") for x in runs),
            "commit_ratio_high": any(x.get("commit_ratio_high") for x in runs),
            "min_free_gb": min(
                [x["free_gb_after"] for x in runs
                 if x.get("free_gb_after") is not None] or [None]),
            "statuses": [x["status"] for x in runs],
        })

    status_counts: dict[str, int] = {}
    for r in rows:
        for st in r["statuses"]:
            status_counts[st] = status_counts.get(st, 0) + 1

    flaky = [r for r in rows if r["classification"] == "flaky"]
    stable_pass = [r for r in rows if r["classification"] == "stable_pass"]
    stable_fail = [r for r in rows if r["classification"] == "stable_fail"]
    mean_rate = sum(r["passes"] for r in rows) / max(1, sum(r["runs"] for r in rows))
    unmeasured = [r for r in rows if r["classification"] == "unmeasured"]

    report = {
        "date": time.strftime("%Y-%m-%d"),
        "harness_version": HARNESS_VERSION,
        "code": __import__("agent.provenance", fromlist=["code_identity"]).code_identity(),
        "budget_scale": args.budget_scale,
        "memory_suspect_keys": [r["key"] for r in rows if r.get("memory_suspect")],
        "cache_hit_ratio_median": _median_or_none(
            [x["cache_hit_ratio"] for xs in results.values() for x in xs
             if x.get("cache_hit_ratio") is not None
             and not x.get("unmeasured")]),
        "cache_measured_tasks": sum(
            1 for xs in results.values() for x in xs
            if x.get("cache_hit_ratio") is not None),
        "cache_drop_keys": sorted(
            k for k, xs in results.items()
            if any(x.get("cache_drop") for x in xs)),
        "cache_drop_fraction": CACHE_DROP_FRACTION,
        "retrieval_isolation": _isolation_state(ctx),
        "session_boundary": ("at deletion compaction: memory flush (skipped "
                             "under isolation), compact() unchanged, session "
                             "rotated; no summarizer"),
        "channels_enabled": sorted(
            n for n in ("telegram",)
            if getattr(getattr(ctx.config.channels, n, None), "enabled", False)),
        "max_parallel_tools": ctx.config.limits.max_parallel_tools,
        "min_free_gb_any_task": min(
            [r["min_free_gb"] for r in rows
             if r.get("min_free_gb") is not None] or [None]),
        "toolset_version": TOOLSET_VERSION,
        "sandbox_libraries": _library_provenance(),
        "order_seed": args.order_seed,
        "task_order_randomised": True,
        "worker_reloads": reloads,
        "worker_reload_count": len(reloads),
        "worker_reload_cost_s": round(sum(x["cost_s"] for x in reloads), 1),
        "refresh_commit_ratio_threshold": REFRESH_COMMIT_RATIO,
        "abort_excluded_max": ABORT_EXCLUDED_MAX,
        "machine_fault_note": (
            "health telemetry only - a recovered stall produced a valid "
            "measured result and carries no abort authority"),
        "machine_fault_keys": sorted({
            x["key"] for xs in results.values() for x in xs
            if x.get("machine_fault")}),
        "excluded_keys": sorted({
            x["key"] for xs in results.values() for x in xs
            if x.get("excluded")}),
        "stall_rule": (
            f"a call below {int(100 * 0.25)}% of the executor's running median "
            "tok/s is aborted as a machine fault; the task re-runs once and is "
            "EXCLUDED (never failed) if it stalls again"),
        "wall_outlier_keys": [r["key"] for r in rows if r.get("wall_outlier")],
        "commit_ratio_high_keys": [r["key"] for r in rows
                                   if r.get("commit_ratio_high")],
        "wall_outlier_factor": WALL_OUTLIER_FACTOR,
        "wall_outlier_abs_s": WALL_OUTLIER_ABS_S,
        "commit_ratio_suspect_threshold": COMMIT_RATIO_SUSPECT,
        "emergency_free_gb": EMERGENCY_FREE_GB,
        "memory_pressure_at_start": memory_pressure(),
        "free_memory_gb_at_start": free_gb,
        "free_memory_gb_before_reap": before_gb,
        "orphans_reaped": reaped,
        "free_memory_gb_at_end": free_memory_gb(),
        "profile": ctx.config.profile,
        "profile_provenance": ctx.config.profile_provenance,
        "suite": args.suite, "split": args.split, "runs_per_task": args.runs,
        "n_tasks": len(rows),
        "mean_pass_rate": round(mean_rate, 4),
        "stable_pass": len(stable_pass), "stable_fail": len(stable_fail),
        "flaky": len(flaky),
        "wall_s_this_session": round(time.time() - started, 1),
        "wall_s_all_runs": round(
            sum(x["wall_s"] for runs in results.values() for x in runs), 1),
        "status_counts": status_counts,
        "unmeasured": len(unmeasured),
        "unmeasured_keys": [r["key"] for r in unmeasured],
        "partially_unmeasured_keys": unmeasured_keys,
        "done": status_counts.get("done", 0),
        "done_unannounced": status_counts.get("done_unannounced", 0),
        "ended_at_step_limit": status_counts.get("max_steps", 0),
        "ended_budget_exhausted": status_counts.get("budget_exhausted", 0),
        "tier_pass_rates": {
            tier: round(
                sum(r["passes"] for r in rows if r["tier"] == tier)
                / max(1, sum(r["runs"] for r in rows if r["tier"] == tier)), 4)
            for tier in ("easy", "medium", "hard")
            if any(r["tier"] == tier for r in rows)
        },
        "tasks": sorted(rows, key=lambda r: r["key"]),
    }
    write_report(dest, report)

    print(f"\n{'':-<58}")
    print(f"stable pass {len(stable_pass):>3}   stable fail {len(stable_fail):>3}   "
          f"flaky {len(flaky):>3}")
    print(f"mean pass rate {mean_rate:.1%}  "
          f"(done={status_counts.get('done', 0)}, "
          f"done_unannounced={status_counts.get('done_unannounced', 0)})")
    for tier in ("easy", "medium", "hard"):
        t = [r for r in rows if r["tier"] == tier]
        if not t:
            continue
        denom = sum(r["runs"] for r in t)
        if denom == 0:
            print(f"  {tier:<7}    n/a  ({len(t)} tasks, all excluded)")
            continue
        rate = sum(r["passes"] for r in t) / denom
        print(f"  {tier:<7} {rate:>6.1%}  ({len(t)} tasks)")
    excluded_rows = [r for r in rows
                     if any(x.get("excluded")
                            for x in results.get(r["key"], []))]
    if excluded_rows:
        keys = ", ".join(r["key"] for r in excluded_rows)
        print(chr(10) + f"{len(excluded_rows)} task(s) EXCLUDED after two "
              f"machine stalls: {keys}")
        print("Excluded is NOT a failure and NOT a pass. These are out "
              "of the pass rate entirely; re-run them before reporting.")
    if unmeasured:
        print(f"\n{len(unmeasured)} task(s) UNMEASURED - the model server "
              f"failed twice: {', '.join(r['key'] for r in unmeasured)}")
        print("These are excluded from the pass rate. They are not evidence "
              "the model cannot do them; re-run them before reporting.")
    if status_counts:
        print("  ended: " + "  ".join(f"{k}={v}" for k, v in
                                      sorted(status_counts.items())))
    print(f"written to {dest}")

    verdict = 0
    if flaky:
        print(f"\nflaky: {', '.join(r['key'] for r in flaky)}")
    if len(flaky) > args.max_flaky:
        print(f"\nSTOP. {len(flaky)} flaky tasks exceeds the limit of {args.max_flaky}.")
        print("Tighten the task specs before evaluating any skill — a lift "
              "measured against this much noise cannot be interpreted.")
        verdict = 1
    if not (TARGET_BAND[0] <= mean_rate <= TARGET_BAND[1]):
        print(f"\nWARNING. Mean pass rate {mean_rate:.1%} is outside the "
              f"{TARGET_BAND[0]:.0%}-{TARGET_BAND[1]:.0%} band where a lift is "
              f"detectable. Rebalance difficulty before evaluating.")
        verdict = max(verdict, 1)
    if verdict == 0:
        print("\nSuite looks usable. Next: the calibration skill through the gate.")
    ctx.close()
    return verdict


def main(argv=None) -> int:
    from agent.awake import keep_awake

    with keep_awake():
        return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
