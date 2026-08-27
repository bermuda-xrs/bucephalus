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
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import run_task  # noqa: E402
from agent.instrument.report import pareto_points, write_calls_csv  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.models.base import BackendError  # noqa: E402
from agent.models.registry import build_backends  # noqa: E402
from agent.runlock import RunLock, RunLockBusy  # noqa: E402
from agent.workspace import reset_workspace  # noqa: E402
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


def load_suite(spec: str | Path, ctx=None) -> list[dict]:
    """Load a suite by name from the database, or from files.

    `--suite mine` reads the suite authored in the UI; anything that looks like
    a path still reads files, so existing folders keep working.
    """
    text = str(spec)
    if ctx is not None and not Path(text).exists():
        name = text.split("suite:", 1)[-1]
        s = ctx.userdata.get_suite_by_name(name)
        if s is not None:
            return s.to_specs()
        raise SystemExit(
            f"no suite named {name!r} and no such path. Suites: "
            f"{[x['name'] for x in ctx.userdata.list_suites()] or 'none yet'}"
        )

    path = Path(text)
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
    reset_workspace(ctx.config.root, spec.get("artifact_scope") or [],
                    spec.get("fixtures") or {})


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
    ctx = AgentContext.open(args.config, profile=args.profile)
    suite = load_suite(args.suite, ctx)
    out = {}

    if args.executor:
        # Spec 0.1c: the paired-trial harness is not skill-specific. Swap the
        # arms from skill/no-skill to model A/model B and read the result off
        # the same statistics — that is how model choice gets settled from
        # your own workload instead of someone else's leaderboard.
        ctx.config.backends["cloud"].model = args.executor
        ctx.backends = build_backends(ctx.config)
        label = args.label or args.executor
        out[label] = run_arm(ctx, suite, label, skills_on=True)
    else:
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
    # Shares the lock file with the noise floor deliberately: a calibration
    # racing a noise-floor pass corrupts both, and they are the same kind of
    # measurement competing for the same CPU.
    try:
        # Anchored beside the OUT file, resolved against the config's own
        # directory. A bare `--out calibration.json` would otherwise put the
        # lock in whatever the current directory happens to be, so two runs
        # started from different places would not see each other.
        _out = Path(args.out or "runs/x")
        if not _out.is_absolute():
            _out = Path(args.config).resolve().parent / _out
        lock = RunLock(_out.parent / ".measurement.lock", "calibration").acquire()
    except RunLockBusy as e:
        print(f"REFUSING TO START.\n{e}")
        return 2

    ctx = AgentContext.open(args.config, profile=args.profile)

    # The deputy comes OUT, exactly as it does for the noise floor. Two
    # reasons, and the first is fatal to the comparison:
    #
    #   * the noise floor measured the baseline arm with auto_review OFF. If
    #     the calibration runs with it ON, the two arms are not measuring the
    #     same system and the lift is not attributable to the skill.
    #   * an LLM in the decision path is a source of variance in exactly the
    #     signal this run exists to measure, and on this hardware it loads a
    #     SECOND model beside the executor - which is what produced
    #     "500 Internal Server Error ... timed out waiting for llama-server"
    #     on a 15.5 GB machine.
    #
    # Nothing about safety is relaxed: the capability gate is code - scope,
    # taint ceilings, protected paths, trusted-tier parking - and still runs
    # on every action.
    if not args.auto_review:
        ctx.config.autonomy.auto_review = False
        ctx.config.autonomy.approve_scoped_writes = True
        print("auto-review OFF for measurement, matching the noise floor "
              "(code gate still enforced). Pass --auto-review to include the "
              "deputy - but then the baseline arm must be re-measured too.")

    suite = load_suite(args.suite, ctx)

    # The contamination rule is a property of WHICH tasks run, so it has to be
    # expressible here. Without --split the gate runs on all 30 and a skill
    # drafted from the drafting half is scored on tasks it was drafted from —
    # `check_contamination` only catches that when provenance names them, and
    # a hand-written skill names nothing.
    if args.split:
        suite = [s for s in suite
                 if (s.get("output_schema") or {}).get("x-split") == args.split]
        if not suite:
            print(f"no tasks in split {args.split!r}")
            lock.release()
            return 2
    specs = [
        TaskSpec(
            key=s.get("key", s["goal"][:40]),
            goal=s["goal"],
            success_criterion=s.get("success_criterion", "always_pass"),
            output_schema=s.get("output_schema", {}),
            artifact_scope=s.get("artifact_scope", []),
            # Without these the trial runs against whatever the last run left
            # on disk: no input files, or worse, the previous arm's answer.
            fixtures=s.get("fixtures") or {},
            grader_dir=s.get("grader_dir"),
        )
        for s in suite
    ]
    missing = [sp.key for sp in specs if sp.artifact_scope and not sp.fixtures]
    if missing:
        print(f"warning: {len(missing)} tasks declare a scope but no fixtures "
              f"({', '.join(missing[:5])}...); they will run against whatever "
              "is already on disk")
    if len(specs) < ctx.config.gate.min_paired_trials:
        print(
            f"warning: {len(specs)} tasks is below gate.min_paired_trials="
            f"{ctx.config.gate.min_paired_trials}; the gate will report "
            "insufficient, which is the gate working."
        )
    ledger = Path(ctx.config.resolve(args.out)) if args.out else None
    done = _completed_trials(ctx, args.skill_id) if args.resume else set()
    if args.resume:
        print(f"resuming: {len(done)} of {len(specs)} pairs already in the "
              "skill_trials table")

    skipped: list[str] = []
    for i, spec in enumerate(specs):
        if spec.key in done:
            print(f"  {i + 1}/{len(specs)} {spec.key}  (resumed)", flush=True)
            continue
        t0 = time.time()
        try:
            _paired_with_retry(ctx, args.skill_id, spec, seed=i)
        except BackendError as e:
            # Leave the pair UNRECORDED rather than half-recorded: the
            # analysis drops any pair missing an arm, and --resume re-runs a
            # task whose pair is incomplete. Skipping beats fabricating.
            print(f"  {i + 1}/{len(specs)} {spec.key}  SKIPPED - backend "
                  f"error twice: {e}", flush=True)
            skipped.append(spec.key)
            continue
        print(f"  {i + 1}/{len(specs)} {spec.key}  {time.time() - t0:6.1f}s",
              flush=True)

    if skipped:
        print(f"\n{len(skipped)} task(s) SKIPPED on backend errors: "
              f"{', '.join(skipped)}")
        print("Their pairs are incomplete and excluded from the analysis. "
              "Re-run with --resume before reporting.")

    # PRIMARY - every task that ran.
    primary = evaluate_skill(ctx, args.skill_id)

    # SECONDARY - the eval subset, from the SAME trials. No extra compute:
    # the eval tasks are a subset of the ones already run. Contamination-clean
    # but underpowered, and labelled as such wherever it is reported.
    eval_keys = {sp.get("key") for sp in suite
                 if (sp.get("output_schema") or {}).get("x-split") == "eval"}
    secondary = (evaluate_skill(ctx, args.skill_id, task_keys=eval_keys)
                 if eval_keys else None)

    report = {
        "skill_id": args.skill_id,
        "date": time.strftime("%Y-%m-%d"),
        "profile": ctx.config.profile,
        "profile_provenance": ctx.config.profile_provenance,
        "models": {n: b.cfg.model for n, b in ctx.backends.items()},
        "split_run": args.split,
        "primary": {
            "scope": f"all {len(specs)} tasks that ran",
            "verdict": primary.verdict, "reason": primary.reason,
            **primary.detail,
            "caveat": (
                "The calibration skill was written by an author who saw the "
                "entire suite, so no split is clean with respect to the "
                "author. This is a POSITIVE CONTROL on the instrument - does "
                "the detector fire when a real effect exists - not a claim "
                "that the skill generalizes."
            ),
        },
        "caveats": CAVEATS,
    }
    if secondary is not None:
        report["secondary"] = {
            "scope": f"eval subset, {secondary.detail.get('n', 0)} tasks",
            "verdict": secondary.verdict, "reason": secondary.reason,
            **secondary.detail,
            "caveat": (
                "Contamination-clean with respect to the drafting split, but "
                "UNDERPOWERED at this n. Report alongside the primary, never "
                "instead of it."
            ),
        }

    print()
    _print_arm("PRIMARY", report["primary"])
    if secondary is not None:
        _print_arm("SECONDARY", report["secondary"])

    if ledger is not None:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        tmp = ledger.with_suffix(ledger.suffix + ".tmp")
        tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        os.replace(tmp, ledger)
        print(f"\nwritten to {ledger}")

    if primary.verdict == "promote":
        print("\ngate passed. Promotion still needs a human: "
              f"alpha skills --gate {args.skill_id} --yes")
    ctx.close()
    lock.release()
    return 0


def _paired_with_retry(ctx, skill_id: str, spec, seed: int, attempts: int = 2):
    """One retry on a model-server failure before giving up on the pair."""
    for attempt in range(1, attempts + 1):
        try:
            return run_paired_trial(ctx, skill_id, spec, seed=seed)
        except BackendError as e:
            if attempt == attempts:
                raise
            print(f"    {spec.key}: backend error, retrying once - {e}",
                  flush=True)
            time.sleep(15)


def _completed_trials(ctx, skill_id: str) -> set[str]:
    """Task keys with BOTH arms already recorded.

    A half-finished pair is not resumable: `fetch_paired_outcomes` drops any
    pair missing an arm, so the task must be re-run rather than counted.
    """
    rows = ctx.conn.execute(
        "SELECT task_key, arm FROM skill_trials WHERE skill_id=?", (skill_id,)
    ).fetchall()
    arms: dict[str, set[str]] = {}
    for key, arm in rows:
        arms.setdefault(key, set()).add(arm)
    return {k for k, a in arms.items() if {"control", "skill"} <= a}


def _print_arm(label: str, d: dict) -> None:
    print(f"{label} - {d['scope']}")
    print(f"  verdict     {d['verdict']}  ({d['reason']})")
    print(f"  n={d.get('n')}  b={d.get('b')} (skill wins)  "
          f"c={d.get('c')} (control wins)  discordant={d.get('discordant')}")
    print(f"  lift        {d.get('lift_pp')}pp     p={d.get('p')}")
    print(f"  caveat      {d['caveat']}")
    print()


def main() -> int:
    p = argparse.ArgumentParser(description="alpha test 1 benchmark harness")
    p.add_argument("--config", default="config.yaml")
    # Declared, enforced before any work starts, and recorded on the result.
    # Without it these two commands defaulted to profile=mixed while the rest
    # of the harness ran local, so the most important measurement in the
    # project was the one not checking what it was allowed to touch.
    p.add_argument("--profile", choices=["local", "mixed"],
                   help="enforced backend profile; 'local' forbids cloud "
                        "backends and provider credentials")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("arms", help="cost-vs-pass-rate across configurations")
    a.add_argument("--suite", default="bench/tasks")
    a.add_argument("--out", default="runs/bench.json")
    a.add_argument("--only")
    a.add_argument("--executor", help="override the executor model; makes this a model arm")
    a.add_argument("--label", help="name for this arm in the report")
    a.set_defaults(fn=cmd_arms)

    q = sub.add_parser("paired", help="M9 paired trials for one skill")
    q.add_argument("skill_id")
    q.add_argument("--suite", default="bench/tasks")
    q.add_argument("--out", default="runs/calibration.json",
                   help="write the two-number report here")
    q.add_argument("--auto-review", action="store_true",
                   help="keep the LLM deputy in the loop. Adds variance to the "
                        "signal being measured, and the baseline arm must be "
                        "re-measured the same way or the arms differ")
    q.add_argument("--resume", action="store_true",
                   help="skip pairs already recorded in skill_trials; a killed "
                        "calibration restarts where it stopped")
    q.add_argument("--split", choices=["drafting", "eval"],
                   help="restrict trials to one split; use 'eval' for any "
                        "skill that could have been informed by the drafting half")
    q.set_defaults(fn=cmd_paired)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
