"""Part 13 — the benchmark harness.

Two jobs:

  * `arms`   — run the same task suite under several configurations and emit a
               cost-versus-pass-rate Pareto frontier. NOT a single score.
  * `paired` — run the M9 paired-trial harness for one skill.

The M9 harness is not skill-specific: swap the arms from skill/no-skill to
model A/model B, run 30 paired trials on your own tasks, and read the result
off the same statistics. That is how you settle model choice empirically
rather than from someone else's leaderboard.

Reporting rule, enforced by `_provenance`: every number carries the date, the
model version, and the scaffold version, and contamination caveats go in the
same paragraph as the number — never a footnote.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import run_task  # noqa: E402
from agent.instrument.report import pareto_points, write_calls_csv  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.skills.gate import TaskSpec, evaluate_skill, run_paired_trial  # noqa: E402

CAVEATS = [
    "Token counts are local estimates unless calibrated; see src/agent/tokens.py.",
    "Public coding benchmarks are contamination-inflated by roughly 5-15 points, "
    "and an audit found ~59% of the hardest SWE-bench Verified tasks have tests "
    "that would not catch the intended bug. A reported 90% is realistically 75-80%.",
    "Identical GAIA problems score anywhere from ~45% to ~92% depending only on "
    "the scaffold around the model.",
    "Published agent costs have been misestimated by up to 100%. These numbers "
    "are cost-controlled but single-run unless n is stated.",
]


def load_suite(path: Path) -> list[dict]:
    if path.is_dir():
        return [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(path.glob("*.json"))
        ]
    return json.loads(path.read_text(encoding="utf-8"))


def _provenance(ctx, arm: str) -> dict:
    return {
        "arm": arm,
        "date": time.strftime("%Y-%m-%d"),
        "models": {n: b.cfg.model for n, b in ctx.backends.items()},
        "scaffold_version": __import__("agent").__version__,
        "local_tier": ctx.config.active_local_tier,
        "effort": {n: b.cfg.effort for n, b in ctx.backends.items()},
        "caveats": CAVEATS,
    }


def provision(ctx, spec: dict) -> None:
    """Create the files a task expects to find.

    A benchmark whose fixtures you have to place by hand is a benchmark nobody
    reruns. Each task declares its own inputs under `fixtures`, so the suite is
    self-contained and every arm starts from identical bytes.
    """
    for rel, content in (spec.get("fixtures") or {}).items():
        path = ctx.config.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def run_arm(ctx, suite: list[dict], arm: str, skills_on: bool) -> dict:
    """One configuration over the whole suite. Tasks are labelled for the chart."""
    results = []
    for spec in suite:
        provision(ctx, spec)
        contract = TaskContract(
            goal=f"{spec['goal']} [arm:{arm}]",
            success_criterion=spec.get("success_criterion", "always_pass"),
            output_schema=spec.get("output_schema", {}),
            artifact_scope=spec.get("artifact_scope", []),
        )
        budget = Budget(
            usd=spec.get("budget_usd", ctx.config.limits.default_task_budget_usd),
            seconds=spec.get("budget_seconds", ctx.config.limits.default_task_budget_seconds),
        )
        task = create_task(ctx.conn, ctx.log, contract, budget)
        if not skills_on:
            ctx.set_frozen_skills(task.id, [])
        t0 = time.time()
        result = run_task(ctx, task.id)
        m = ctx.telemetry.metrics(task.id)
        results.append(
            {
                "key": spec.get("key", spec["goal"][:40]),
                "task_id": task.id,
                "status": result.status,
                "passed": result.status == "done",
                "cost_usd": m.cost_usd,
                "paid_tokens": m.paid_tokens,
                "free_tokens": m.free_tokens,
                "wall_s": round(time.time() - t0, 2),
                "cache_hit_ratio": m.cache_hit_ratio,
            }
        )
    n = len(results) or 1
    return {
        "provenance": _provenance(ctx, arm),
        "n": len(results),
        "pass_rate": sum(r["passed"] for r in results) / n,
        "mean_usd_per_task": sum(r["cost_usd"] for r in results) / n,
        "mean_wall_s": sum(r["wall_s"] for r in results) / n,
        "results": results,
    }


def cmd_arms(args) -> int:
    ctx = AgentContext.open(args.config)
    suite = load_suite(Path(args.suite))
    out = {}
    for arm, skills_on in (("skills-off", False), ("skills-on", True)):
        if args.only and arm != args.only:
            continue
        out[arm] = run_arm(ctx, suite, arm, skills_on)

    report = {
        "pareto": pareto_points(ctx.conn, ctx.telemetry),
        "arms": out,
        "how_to_read": (
            "x = mean USD/task, y = pass rate. Report three ways — bare model, "
            "model + scaffold, full system — with date and versions."
        ),
    }
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_calls_csv(ctx.conn, dest.with_suffix(".calls.csv"))
    print(json.dumps({k: {"pass_rate": v["pass_rate"],
                          "mean_usd_per_task": round(v["mean_usd_per_task"], 5)}
                      for k, v in out.items()}, indent=2))
    print(f"\nwritten to {dest}")
    ctx.close()
    return 0


def cmd_paired(args) -> int:
    ctx = AgentContext.open(args.config)
    suite = load_suite(Path(args.suite))
    specs = [
        TaskSpec(
            key=s.get("key", s["goal"][:40]),
            goal=s["goal"],
            success_criterion=s.get("success_criterion", "always_pass"),
            output_schema=s.get("output_schema", {}),
            artifact_scope=s.get("artifact_scope", []),
        )
        for s in suite
    ]
    if len(specs) < ctx.config.gate.min_paired_trials:
        print(
            f"warning: {len(specs)} tasks is below gate.min_paired_trials="
            f"{ctx.config.gate.min_paired_trials}; the gate will report "
            "insufficient, which is the gate working."
        )
    for i, spec in enumerate(specs):
        run_paired_trial(ctx, args.skill_id, spec, seed=i)
        print(f"  {i + 1}/{len(specs)} {spec.key}")

    decision = evaluate_skill(ctx, args.skill_id)
    print(json.dumps({"verdict": decision.verdict, "reason": decision.reason,
                      "detail": decision.detail}, indent=2))
    if decision.verdict == "promote":
        print("\ngate passed. promotion still needs a human: alpha skills --gate "
              f"{args.skill_id} --yes")
    ctx.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="alpha test 1 benchmark harness")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("arms", help="cost-vs-pass-rate across configurations")
    a.add_argument("--suite", default="bench/tasks")
    a.add_argument("--out", default="runs/bench.json")
    a.add_argument("--only")
    a.set_defaults(fn=cmd_arms)

    q = sub.add_parser("paired", help="M9 paired trials for one skill")
    q.add_argument("skill_id")
    q.add_argument("--suite", default="bench/tasks")
    q.set_defaults(fn=cmd_paired)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
