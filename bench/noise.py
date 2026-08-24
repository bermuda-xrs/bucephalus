"""Noise floor — run the baseline arm N times and classify each task.

Run this BEFORE any skill evaluation. A paired trial compares two arms on the
same task; if a task's own outcome swings run to run, the comparison is
measuring the swing rather than the skill. You cannot tell a 6-point lift from
noise without knowing how loud the noise is.

Classification per task:

  * ``stable_pass``  — passed every run. Contributes no discordant pairs, so
                       it cannot show a lift; it can still catch a regression.
  * ``stable_fail``  — failed every run. Headroom for a skill to work in.
  * ``flaky``        — mixed. Carries information AND noise, and at small n
                       is the main way a false positive gets in.

The stop rule is a real stop, not advice: more than `--max-flaky` flaky tasks
and the suite is not an instrument yet. Tighten the task specs — usually an
under-specified output format or a tolerance doing too much work — rather than
running an evaluation whose result you would not be able to interpret.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import run_task  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402

TARGET_BAND = (0.35, 0.65)   # mean pass rate where measurement has power


def provision(ctx, fixtures: dict) -> None:
    for rel, content in (fixtures or {}).items():
        path = ctx.config.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def run_once(ctx, spec: dict, run_ix: int) -> dict:
    provision(ctx, spec.get("fixtures"))
    contract = TaskContract(
        goal=spec["goal"],
        success_criterion=spec.get("success_criterion", "verify_program"),
        output_schema=spec.get("output_schema", {}),
        artifact_scope=spec.get("artifact_scope", []),
    )
    budget = Budget(
        usd=spec.get("budget_usd", 0.0) or 0.0,
        seconds=spec.get("budget_seconds")
                or ctx.config.limits.default_task_budget_seconds,
    )
    task = create_task(ctx.conn, ctx.log, contract, budget)
    ctx.set_frozen_skills(task.id, [])          # baseline: no skills
    t0 = time.time()
    result = run_task(ctx, task.id)
    m = ctx.telemetry.metrics(task.id)
    return {
        "run": run_ix,
        "task_id": task.id,
        "status": result.status,
        "passed": result.status == "done",
        "wall_s": round(time.time() - t0, 1),
        "free_tokens": m.free_tokens,
        "cost_usd": m.cost_usd,
    }


def classify(passes: list[bool]) -> str:
    if all(passes):
        return "stable_pass"
    if not any(passes):
        return "stable_fail"
    return "flaky"


def main() -> int:
    ap = argparse.ArgumentParser(description="measure the suite's noise floor")
    ap.add_argument("--config", default="config.local.yaml")
    ap.add_argument("--profile", default="local",
                    help="enforced backend profile; 'local' forbids cloud backends")
    ap.add_argument("--suite", default="aero_calc")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-flaky", type=int, default=8)
    ap.add_argument("--split", choices=["drafting", "eval"])
    ap.add_argument("--limit", type=int, help="first N tasks only (for a smoke check)")
    ap.add_argument("--out", default="runs/noise-floor.json")
    ap.add_argument("--auto-review", action="store_true",
                    help="keep the LLM deputy in the loop (adds variance)")
    args = ap.parse_args()

    ctx = AgentContext.open(args.config, profile=args.profile)

    # The deputy is an LLM, and an LLM in the decision path is a source of
    # variance in exactly the signal this suite exists to measure. Observed on
    # granite4.1:8b: the same class of action approved once and denied four
    # times with mutually contradictory reasons, including one denial of a
    # path that was inside artifact_scope. A benchmark run measures the
    # executor, so the deputy comes out by default.
    #
    # Nothing about safety is relaxed. The capability gate is code — scope,
    # taint ceilings, protected paths, trusted-tier parking — and it still
    # runs on every action. What is removed is the nondeterministic layer.
    if not args.auto_review:
        ctx.config.autonomy.auto_review = False
        ctx.config.autonomy.approve_scoped_writes = True
        print("auto-review OFF for measurement (code gate still enforced: "
              "scope, taint ceilings, protected paths, trusted-tier parking). "
              "Pass --auto-review to include the deputy.")
    suite = ctx.userdata.get_suite_by_name(args.suite)
    if suite is None:
        print(f"no suite named {args.suite!r}. Load it first:\n"
              f"  python bench/suites/aero_calc.py --load --config {args.config}")
        return 2

    specs = suite.to_specs()
    if args.split:
        specs = [s for s in specs
                 if (s.get("output_schema") or {}).get("x-split") == args.split]
    if args.limit:
        specs = specs[: args.limit]
    if not specs:
        print("no tasks matched")
        return 2

    print(f"noise floor · {len(specs)} tasks x {args.runs} runs = "
          f"{len(specs) * args.runs} runs · profile={ctx.config.profile}")
    print(f"backends: {ctx.config.profile_provenance.get('backends')}\n")

    results: dict[str, list[dict]] = {}
    started = time.time()
    for r in range(args.runs):
        for i, spec in enumerate(specs, 1):
            key = spec["key"]
            out = run_once(ctx, spec, r)
            results.setdefault(key, []).append(out)
            print(f"  run {r + 1}/{args.runs}  {i:>2}/{len(specs)}  {key:<6} "
                  f"{out['status']:<16} {out['wall_s']:>6.1f}s", flush=True)

    rows = []
    for key, runs in results.items():
        passes = [x["passed"] for x in runs]
        spec = next(s for s in specs if s["key"] == key)
        rows.append({
            "key": key,
            "tier": (spec.get("output_schema") or {}).get("x-tier", "?"),
            "split": (spec.get("output_schema") or {}).get("x-split", "?"),
            "classification": classify(passes),
            "passes": sum(passes), "runs": len(passes),
            "mean_wall_s": round(statistics.mean(x["wall_s"] for x in runs), 1),
            "statuses": [x["status"] for x in runs],
        })

    flaky = [r for r in rows if r["classification"] == "flaky"]
    stable_pass = [r for r in rows if r["classification"] == "stable_pass"]
    stable_fail = [r for r in rows if r["classification"] == "stable_fail"]
    mean_rate = sum(r["passes"] for r in rows) / max(1, sum(r["runs"] for r in rows))

    report = {
        "profile": ctx.config.profile,
        "profile_provenance": ctx.config.profile_provenance,
        "suite": args.suite, "split": args.split, "runs_per_task": args.runs,
        "n_tasks": len(rows),
        "mean_pass_rate": round(mean_rate, 4),
        "stable_pass": len(stable_pass), "stable_fail": len(stable_fail),
        "flaky": len(flaky),
        "wall_s_total": round(time.time() - started, 1),
        "tasks": sorted(rows, key=lambda r: r["key"]),
    }
    dest = Path(ctx.config.resolve(args.out))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\n{'':-<58}")
    print(f"stable pass {len(stable_pass):>3}   stable fail {len(stable_fail):>3}   "
          f"flaky {len(flaky):>3}")
    print(f"mean pass rate {mean_rate:.1%}")
    for tier in ("easy", "medium", "hard"):
        t = [r for r in rows if r["tier"] == tier]
        if t:
            rate = sum(r["passes"] for r in t) / sum(r["runs"] for r in t)
            print(f"  {tier:<7} {rate:>6.1%}  ({len(t)} tasks)")
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


if __name__ == "__main__":
    raise SystemExit(main())
