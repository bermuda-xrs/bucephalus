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
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import run_task  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.runlock import RunLock, RunLockBusy  # noqa: E402
from agent.workspace import reset_workspace  # noqa: E402

TARGET_BAND = (0.35, 0.65)   # mean pass rate where measurement has power

# The baseline arm carries no skills. `--skill` swaps in exactly one, which
# turns this script into a cheap sighting shot: before committing hours to a
# full paired gate, run the same handful of tasks both ways and see whether
# the skill moves anything at all. A skill that shifts nothing on five tasks
# will not suddenly find signal on thirty.
SKILLS_FOR_ARM: list = []


def provision(ctx, spec: dict) -> None:
    """Rebuild the task workspace from its declared fixtures.

    Writing the fixtures over the top is not enough: `verify_program` grades
    files on disk, so run 2 of a repeat pass would be scored against run 1's
    `output/answer.json` whenever run 2 fails to produce one. A flaky task
    then reads as stable, which is the one thing this script exists to detect.
    """
    reset_workspace(ctx.config.root, spec.get("artifact_scope") or [],
                    spec.get("fixtures") or {})


def run_once(ctx, spec: dict, run_ix: int) -> dict:
    provision(ctx, spec)
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
    ctx.set_frozen_skills(task.id, SKILLS_FOR_ARM)
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


def ledger_path(out: str | Path) -> Path:
    """Sidecar beside the report: one JSON line per completed run."""
    p = Path(out)
    return p.with_suffix(p.suffix + ".partial.jsonl")


def run_signature(args, n_specs: int) -> dict:
    """What a ledger must agree with before its rows may be reused.

    Resuming across a changed suite, split, run count, or PROFILE would
    silently blend two different measurements into one report — the same
    class of error `profiles.py` exists to prevent. The profile belongs here
    for the same reason it is on the report: half a pass measured local and
    half measured mixed is not a measurement, and nothing downstream could
    tell.
    """
    # `runs` is deliberately NOT part of the signature. Each row is keyed
    # (run_index, task_key), so rows from a --runs 1 pass are the same work
    # as rows 0 of a --runs 3 pass. Excluding it is what lets a pass-1-then-
    # decide plan resume into passes 2 and 3 without repeating pass 1 —
    # hours, on this hardware. Everything that would change WHAT is being
    # measured is included.
    return {"suite": args.suite, "split": args.split,
            "limit": args.limit, "tier": sorted(args.tier) if args.tier else None,
            "only": sorted(args.only) if args.only else None,
            "skill": args.skill,
            "profile": args.profile, "n_specs": n_specs}


def append_run(path: Path, sig: dict, record: dict) -> None:
    """Append one completed run and flush it to the OS immediately.

    A multi-hour pass that holds every result in memory and writes once at
    the end loses ALL of it to a kill at hour three. One line per task, and
    at most one task's work is ever at risk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    first = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if first:
            fh.write(json.dumps({"_signature": sig}) + "\n")
        fh.write(json.dumps(record) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_completed(path: Path, sig: dict) -> dict[tuple[int, str], dict]:
    """Rows from a previous attempt, keyed (run_index, task_key).

    A truncated final line — the signature of a kill mid-write — is dropped
    rather than guessed at.
    """
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
    """Atomic: a kill during the write must not leave a half-parsed report."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(tmp, dest)


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
    ap.add_argument("--skill", help="run WITH this skill (name or id) instead of bare")
    ap.add_argument("--resume", action="store_true",
                    help="reuse completed runs from the .partial.jsonl ledger "
                         "beside --out; a killed pass restarts where it stopped")
    args = ap.parse_args()

    # One measurement process at a time. Two concurrent runs measure their
    # tasks under CPU contention, which can manufacture `budget_exhausted` on
    # a task that would otherwise finish - the suite then looks harder than it
    # is, for reasons that have nothing to do with the tasks.
    try:
        _out = Path(args.out)
        if not _out.is_absolute():
            _out = Path(args.config).resolve().parent / _out
        lock = RunLock(_out.parent / ".measurement.lock", "noise floor").acquire()
    except RunLockBusy as e:
        print(f"REFUSING TO START.\n{e}")
        return 2

    ctx = AgentContext.open(args.config, profile=args.profile)

    global SKILLS_FOR_ARM
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
    if args.only:
        wanted = set(args.only)
        specs = [s for s in specs if s["key"] in wanted]
        missing = wanted - {s["key"] for s in specs}
        if missing:
            print(f"no such task(s): {', '.join(sorted(missing))}")
            lock.release()
            return 2
    if args.tier:
        specs = [s for s in specs
                 if (s.get("output_schema") or {}).get("x-tier") in set(args.tier)]
    if args.limit:
        specs = specs[: args.limit]
    if not specs:
        print("no tasks matched")
        lock.release()
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
        # Never silently extend someone else's ledger.
        raise SystemExit(
            f"{ledger} already exists. Pass --resume to continue that pass, "
            "or delete it / choose another --out to start fresh."
        )

    results: dict[str, list[dict]] = {}
    started = time.time()
    for r in range(args.runs):
        for i, spec in enumerate(specs, 1):
            key = spec["key"]
            cached = done.get((r, key))
            if cached is not None:
                results.setdefault(key, []).append(cached)
                print(f"  run {r + 1}/{args.runs}  {i:>2}/{len(specs)}  {key:<6} "
                      f"{cached['status']:<16} {cached['wall_s']:>6.1f}s  (resumed)",
                      flush=True)
                continue
            out = run_once(ctx, spec, r)
            out["key"] = key
            results.setdefault(key, []).append(out)
            # Before the print, so a kill between them loses nothing.
            append_run(ledger, sig, out)
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

    # Why a pass ended matters as much as whether it passed, and the three
    # terminal statuses mean genuinely different things:
    #
    #   max_steps         burned all 6 turns. Usually the agent looping,
    #                     rather than the task being hard.
    #   plateau           three identical scores; more turns bought nothing.
    #   budget_exhausted  ran out of TIME, not attempts. The task may not
    #                     have been failed on the physics at all.
    #
    # That last one matters for interpretation and was mis-documented here
    # earlier: an initial reading of the record said budget_exhausted had
    # never fired and largely could not, on the reasoning that max_steps=6
    # binds first. M16 then ended budget_exhausted at 705.6s against its 600s
    # budget on 2026-08-27 — six slow turns on a medium task cross 600s of
    # model time before the step limit is reached. Both limits bind, and
    # which one does is itself a signal.
    #
    # Budget.spent_seconds accumulates MODEL wall time only (context.record ->
    # spend), not sandbox or verification time, so a task's reported wall_s
    # still runs ahead of the budget it is checked against.
    status_counts: dict[str, int] = {}
    for r in rows:
        for st in r["statuses"]:
            status_counts[st] = status_counts.get(st, 0) + 1

    flaky = [r for r in rows if r["classification"] == "flaky"]
    stable_pass = [r for r in rows if r["classification"] == "stable_pass"]
    stable_fail = [r for r in rows if r["classification"] == "stable_fail"]
    mean_rate = sum(r["passes"] for r in rows) / max(1, sum(r["runs"] for r in rows))

    report = {
        "date": time.strftime("%Y-%m-%d"),
        "profile": ctx.config.profile,
        "profile_provenance": ctx.config.profile_provenance,
        "suite": args.suite, "split": args.split, "runs_per_task": args.runs,
        "n_tasks": len(rows),
        "mean_pass_rate": round(mean_rate, 4),
        "stable_pass": len(stable_pass), "stable_fail": len(stable_fail),
        "flaky": len(flaky),
        # Two different numbers, and conflating them understates the cost of
        # a resumed run: this process only ran the tasks it did not resume.
        "wall_s_this_session": round(time.time() - started, 1),
        "wall_s_all_runs": round(
            sum(x["wall_s"] for runs in results.values() for x in runs), 1),
        "status_counts": status_counts,
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
    print(f"mean pass rate {mean_rate:.1%}")
    for tier in ("easy", "medium", "hard"):
        t = [r for r in rows if r["tier"] == tier]
        if t:
            rate = sum(r["passes"] for r in t) / sum(r["runs"] for r in t)
            print(f"  {tier:<7} {rate:>6.1%}  ({len(t)} tasks)")
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
    lock.release()
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
