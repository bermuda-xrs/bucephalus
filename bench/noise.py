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
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import run_task  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.models.base import BackendError  # noqa: E402
from agent.models.ollama import ModelStall, reset_rates  # noqa: E402
from agent import HARNESS_VERSION  # noqa: E402
from agent.proc import (  # noqa: E402
    free_memory_gb, memory_pressure, reap_orphans, worker_rss_gb)

# When a task's wall-clock stops being trustworthy.
#
# The first version of this used the house rule's 2 GB free-memory floor. That
# was WRONG, and wrong in this project's signature way: the 2 GB rule is a
# PREFLIGHT check, taken with no model loaded, while this one runs with the
# model resident. Measured on this hardware, a healthy run with granite4.1:3b
# at 16k context sits at 1.17-1.48 GB available - so the 2 GB test flagged
# 100% of a correct run, and the abort rule built on it would have killed a
# healthy pass within a few tasks.
#
# Two measured anchors, 15.47 GB physical:
#     healthy, 3B resident        available 1.17 GB   commit ratio 1.231
#     pathological, orphan held   available 0.90 GB   commit ratio 1.574
#
# Available memory barely separates those; the commit ratio does. 1.40 sits
# between the two anchors with margin on both sides. The absolute floor is
# kept only as an emergency backstop, well below normal operation.
COMMIT_RATIO_SUSPECT = 1.40
EMERGENCY_FREE_GB = 0.50

# L5. RE-ANCHORED after the L6 verification run. Read this before changing it.
#
# The commit ratio DETECTED the worker leak in Phase 2A - 23 consecutive
# readings climbing 1.231 -> 1.421 without one decrease - and that detection
# was correct and load-bearing. It is NOT usable as the per-task suspect flag,
# and L6 showed why with the fix in place:
#
#   post-fix commit ratio, 10 tasks : 1.278 1.384 1.606 1.608 1.599 1.633
#                                     1.634 1.589 1.571 1.531
#   flagged at 1.40                 : 8 of 10, including M15 at 2.52 GB free
#
# The old "pathological" anchor of 1.574 now sits INSIDE the healthy operating
# range. The two-point calibration was invalid: its healthy anchor (1.223) was
# taken before any task had run and with a different set of applications
# loaded, so it measured the machine's state, not the run's health. Committed
# memory on Windows also releases lazily, so the ratio has heavy hysteresis -
# across seven successful refreshes it moved 1.606->1.609, 1.633->1.634,
# 1.587->1.589, i.e. not at all, while worker RSS returned to 3.252 GB every
# single time.
#
# What DOES discriminate is wall clock against the run's own median. The real
# hazard is a single pathological model call, seen three times now: E02 in L6
# took 2539 s to emit 512 tokens (~0.2 tok/s against a normal 2-3), the v3
# probe hit 2465 s, and the historical orphan incident hit 1499.8 s. Those are
# 15x, 29x and 18x their medians. A ratio test cannot separate them from a
# healthy task; a median test separates them trivially.
#
# So: the ratio is still RECORDED, because it is what caught the leak and a
# future monotonic climb should be visible. It no longer decides the flag.
WALL_OUTLIER_FACTOR = 3.0

# L1. When to reload the model worker mid-run.
#
# Phase 2A aborted at task 23/30 because the worker's commitment grew
# monotonically - 1.231 to 1.421 across 23 tasks, not one decrease, about
# 139 MB per task - until the suspect flag fired six times. An unload plus
# reload reclaims all of it (RSS 4.86 GB -> 3.41 GB, the fresh value).
#
# The refresh trigger sits BELOW the suspect threshold on purpose. Firing at
# 1.35 means the run repairs itself before any task is flagged, so the flag
# keeps its meaning: "this task was measured under pressure" rather than
# "the run has been climbing for a while". If tasks are still flagged after
# this lands, the refresh is not working - which is what L6 checks.
REFRESH_COMMIT_RATIO = 1.35
from agent.runlock import RunLock, RunLockBusy  # noqa: E402
from agent.workspace import reset_workspace  # noqa: E402

TARGET_BAND = (0.35, 0.65)   # mean pass rate where measurement has power

# The baseline arm carries no skills. `--skill` swaps in exactly one, which
# turns this script into a cheap sighting shot: before committing hours to a
# full paired gate, run the same handful of tasks both ways and see whether
# the skill moves anything at all. A skill that shifts nothing on five tasks
# will not suddenly find signal on thirty.
SKILLS_FOR_ARM: list = []
BUDGET_SCALE: float = 1.0


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
        grader_dir=spec.get("grader_dir"),
        output_schema=spec.get("output_schema", {}),
        artifact_scope=spec.get("artifact_scope", []),
    )
    # The suite's time budgets were written against granite4.1:3b. A model
    # ~3x slower hits them before it can finish, so an unscaled comparison
    # measures the budget rather than the model. Scaling is controlling a
    # variable; it does not change what counts as a pass.
    budget = Budget(
        usd=spec.get("budget_usd", 0.0) or 0.0,
        seconds=int((spec.get("budget_seconds")
                     or ctx.config.limits.default_task_budget_seconds)
                    * BUDGET_SCALE),
    )
    task = create_task(ctx.conn, ctx.log, contract, budget)
    ctx.set_frozen_skills(task.id, SKILLS_FOR_ARM)
    # Free memory is read per TASK, not only in preflight. Preflight alone
    # cannot see pressure that arrives mid-run, and this project has already
    # measured what that costs: one model call took 2465 s against a 600 s
    # budget - 29x the median for its rung - and ended `budget_exhausted`.
    # Because that outcome is a TIME limit, memory pressure converts directly
    # into apparent task difficulty, which is the one thing a difficulty
    # measurement must not be allowed to invent.
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
    # The commit-ratio test is retained as a SEPARATE recorded signal rather
    # than as the flag - see the L5 note at the top of this file. The flag
    # itself is decided by the caller, which is the only place that knows the
    # run's median so far.
    ratio_high = bool(worst_ratio is not None
                      and worst_ratio >= COMMIT_RATIO_SUSPECT)
    suspect = bool(low is not None and low < EMERGENCY_FREE_GB)
    return {
        "run": run_ix,
        "task_id": task.id,
        "status": result.status,
        # done_unannounced is a PASS: the artifact verified, the model
        # just never said <final>. Counted separately in the report.
        "passed": result.status in ("done", "done_unannounced"),
        "wall_s": wall,
        "free_gb_before": None if mem_before is None else round(mem_before, 2),
        "free_gb_after": None if mem_after is None else round(mem_after, 2),
        # The flag travels with the row, so a suspect wall-clock stays suspect
        # through resume, through the ledger, and into the report.
        # Emergency free-memory backstop only; the wall-clock outlier test is
        # applied by the caller once a median exists.
        "memory_suspect": suspect,
        "commit_ratio_high": ratio_high,
        "commit_ratio": worst_ratio,
        # L2: the leak, observable rather than inferred. If this climbs
        # monotonically across a run, the L1 refresh is not working.
        "worker_rss_before_gb": rss_before,
        "worker_rss_after_gb": worker_rss_gb(),
        "free_tokens": m.free_tokens,
        "cost_usd": m.cost_usd,
    }


def refresh_worker(ctx) -> dict:
    """Unload and reload the executor model. Returns what it cost.

    Reclaims the worker leak (L1). The cost is a cold load - about 58 s on
    this hardware - and it is RECORDED against the refresh rather than left
    to smear into the wall-clock of whichever task happened to follow it.
    That distinction is the whole reason this is reported: a task that looks
    slow because it paid for a reload is not a hard task.
    """
    b = ctx.config.backends["cloud"]
    url = b.endpoint.rstrip("/") + "/api/generate"
    t0 = time.time()
    before = worker_rss_gb()
    try:
        _ollama_post(url, {"model": b.model, "keep_alive": 0})
        time.sleep(3.0)                       # let the OS reclaim the pages
        _ollama_post(url, {"model": b.model, "prompt": "ok", "stream": False,
                           "keep_alive": "30m",
                           "options": {"num_ctx": b.usable_context_window,
                                       "num_predict": 1}})
    except Exception as e:  # noqa: BLE001 - a failed refresh must not kill a run
        return {"ok": False, "error": str(e),
                "cost_s": round(time.time() - t0, 1),
                "rss_before_gb": before, "rss_after_gb": worker_rss_gb()}
    return {"ok": True, "cost_s": round(time.time() - t0, 1),
            "rss_before_gb": before, "rss_after_gb": worker_rss_gb()}


def _ollama_post(url: str, body: dict) -> None:
    import urllib.request

    req = urllib.request.Request(
        url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=600).read()


def run_with_backend_retry(ctx, spec: dict, run_ix: int, key: str) -> dict:
    """A model-server failure is NOT a task failure.

    An ollama ReadTimeout on H27 killed a ten-task pass outright: one slow
    call, and every remaining task was lost. In a five-hour calibration that
    is the difference between a result and a wasted night.

    But the fix must not be to swallow it. Recording a backend error as
    `passed=False` would count a network problem as evidence the model cannot
    do the task - the same silent-0% trap as a verifier that cannot find its
    script. So it gets its own status, is excluded from the pass rate, and is
    reported loudly.
    """
    # A STALL is not a backend error and not a task outcome. The task budget
    # is wall clock and `budget_exhausted` is scored as a failure, so a
    # machine stall silently converts a task that would have passed into one
    # that failed - and in a paired trial that manufactures a discordant pair
    # from a machine event. Interleaving arms cannot help, because a single
    # stalled call lands on exactly one arm by definition.
    #
    # So: abort, re-run the task once, and if it stalls again mark it
    # EXCLUDED. Excluded is reported as excluded. It is never a failure, and
    # never quietly dropped either - a silently discarded task is how a suite
    # gets easier without anyone deciding that it should.
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
            }
        except BackendError:
            break

    for attempt in (1, 2):
        try:
            return run_once(ctx, spec, run_ix)
        except BackendError as e:
            if attempt == 1:
                print(f"    {key}: backend error, retrying once - {e}", flush=True)
                time.sleep(10)
                continue
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
            }


def assert_machine_fault_never_scored_as_failure(row: dict) -> None:
    """S4. A machine event must never reach the pass rate as a failure.

    Both aborted Phase 2A attempts violated exactly this and neither was
    caught by an abort condition, because the mis-scored tasks were recorded
    as `crashed` rather than as machine faults. It is cheap to enforce
    permanently, so it is enforced permanently.

    Two ways it can be violated:

      * an EXCLUDED task that is still measured - it would depress the pass
        rate with a machine event, and
      * a task whose status is `crashed` carrying a stall or timeout cause -
        which is the generic-catch bug returning by another door.
    """
    if row.get("excluded") and not row.get("unmeasured"):
        raise AssertionError(
            f"{row.get('key')}: excluded_machine_fault must be unmeasured, "
            "or a machine event depresses the pass rate")
    status = str(row.get("status") or "")
    if status == "crashed":
        # ANY crash aborts. The earlier version of this guard looked for
        # evidence of a stall in the row - a `stalls` key, or "timed out" in
        # an error string - and a crashed row carrying NEITHER slipped
        # straight through it. That is what an injected transport timeout
        # produced, and the guard written to catch exactly that case passed it.
        #
        # So the test is no longer "does this crash look like a machine
        # event". Every machine event now has its own terminal status, which
        # makes `crashed` unreachable for one; if it appears, a path was
        # missed and the run must stop rather than score it as a failure.
        # Requiring the guard to RECOGNISE the cause is what let two aborted
        # attempts through, and recognition is the part that keeps failing.
        raise AssertionError(
            f"{row.get('key')}: status `crashed`, which the suite scores as "
            "FAILED. Machine events have their own terminal statuses, so a "
            "crash means a path was missed. Abort and diagnose rather than "
            f"scoring it. stalls={row.get('stalls')} error={row.get('error')}")
    if row.get("machine_fault") and row.get("passed") and row.get("excluded"):
        raise AssertionError(
            f"{row.get('key')}: excluded tasks cannot also be passes")


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
    # order_seed IS part of the signature. Rows are keyed (run, task_key) so
    # resume would work regardless, but two orders mean two different sets of
    # measurement conditions per task, and blending them silently is exactly
    # what this signature exists to prevent.
    return {"suite": args.suite, "split": args.split,
            "executor": args.executor, "order_seed": args.order_seed,
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
    args = ap.parse_args()

    # L3: a seed is always chosen and always recorded, so a run is
    # replayable even when nobody asked for a particular order.
    if args.order_seed is None:
        args.order_seed = random.randrange(1, 2**31 - 1)
        print(f"task order seed (generated): {args.order_seed}")
    else:
        print(f"task order seed (given): {args.order_seed}")

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

    # Preflight. An orphan's memory is charged to whatever runs next: on this
    # box a stray llama-server holding 5.9 GB turned an 88s model call into a
    # 1499.8s one. Wall clock is the cost axis, so reap BEFORE measuring.
    before_gb = free_memory_gb()
    reaped = reap_orphans()
    free_gb = free_memory_gb()
    if reaped:
        print(f"reaped {len(reaped)} orphaned worker(s): {reaped}")

    # L4. This used the same 2 GB free-memory rule the per-task check used,
    # and it was wrong for the same reason: 2 GB is a NO-MODEL-LOADED figure,
    # so it warns spuriously whenever the worker is legitimately warm. Both
    # call sites now read the one function, and
    # tests/test_memory_gate_single_source.py fails if a third copy of the
    # rule appears.
    press = memory_pressure()
    if press:
        print(f"memory at start: {press.get('available_gb')} GB available, "
              f"commit ratio {press.get('commit_ratio')} "
              f"(suspect >= {COMMIT_RATIO_SUSPECT}, "
              f"refresh >= {REFRESH_COMMIT_RATIO})")
        rss0 = worker_rss_gb()
        if rss0 is not None:
            print(f"model worker resident at start: {rss0} GB")
        if (press.get("commit_ratio") or 0) >= COMMIT_RATIO_SUSPECT:
            print("WARNING: already at or above the suspect commit ratio "
                  "before the first task. Paging rewrites a seconds-based "
                  "measurement - one call has been seen 18x slower.")

    ctx = AgentContext.open(args.config, profile=args.profile)

    if args.executor:
        # Same suite, same harness, different model: that is what makes the
        # error SIGNATURE comparable between executors.
        from agent.models.registry import build_backends

        ctx.config.backends["cloud"].model = args.executor
        ctx.backends = build_backends(ctx.config)
        # Generation rate is a property of the EXECUTOR. Carrying 3B rates
        # into an 8B run would make every 8B call look like a stall, and the
        # 8B is legitimately 10-16x slower per token.
        reset_rates()
        ctx.config.profile_provenance["executor_override"] = args.executor
        # profile_provenance["backends"] was captured at config load, BEFORE
        # this override, so the report header named the wrong model while the
        # run used the right one. Refresh it, or every 8B result reads as 3B.
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
    reloads: list[dict] = []
    for r in range(args.runs):
        # A fresh permutation per run index, derived from the recorded seed so
        # the whole thing replays exactly.
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
            # L1: repair the worker leak BEFORE it can flag a task, so the
            # suspect flag keeps meaning "measured under pressure" rather
            # than "the run has been climbing for a while".
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

            # A task far off the run's own median is the signature of the
            # pathological single call this project keeps hitting - 2539 s for
            # 512 tokens in L6, 2465 s in the v3 probe, 1499.8 s historically.
            # Computed against completed tasks so far, which is why it lives
            # here and not in run_once.
            prior = [x["wall_s"] for xs in results.values() for x in xs
                     if not x.get("unmeasured")]
            if len(prior) >= 3:
                med = statistics.median(prior)
                out["wall_outlier"] = bool(med > 0 and
                                           out["wall_s"] > WALL_OUTLIER_FACTOR * med)
                out["wall_vs_median"] = round(out["wall_s"] / med, 2) if med else None
            else:
                out["wall_outlier"] = False
                out["wall_vs_median"] = None
            if out["wall_outlier"]:
                out["memory_suspect"] = True
            out["reload_before_this_task"] = bool(
                reloads and reloads[-1]["before_task"] == key
                and reloads[-1]["run"] == r)
            results.setdefault(key, []).append(out)
            # Before the print, so a kill between them loses nothing.
            append_run(ledger, sig, out)
            flag = ""
            if out.get("wall_outlier"):
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
        # A backend error is not an observation. Counting it would let a model
        # server hiccup masquerade as task difficulty.
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
            # A task is memory-suspect if ANY of its runs dipped below the
            # floor. Its pass/fail still counts - the artifact either verified
            # or it did not - but its SECONDS are not a measurement, and the
            # cost axis of this project is seconds.
            "memory_suspect": any(x.get("memory_suspect") for x in runs),
            "wall_outlier": any(x.get("wall_outlier") for x in runs),
            "commit_ratio_high": any(x.get("commit_ratio_high") for x in runs),
            "min_free_gb": min(
                [x["free_gb_after"] for x in runs
                 if x.get("free_gb_after") is not None] or [None]),
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
    # denominator counts only real observations
    mean_rate = sum(r["passes"] for r in rows) / max(1, sum(r["runs"] for r in rows))
    unmeasured = [r for r in rows if r["classification"] == "unmeasured"]

    report = {
        "date": time.strftime("%Y-%m-%d"),
        "harness_version": HARNESS_VERSION,
        "budget_scale": args.budget_scale,
        # Per-task memory, summarised. Preflight numbers alone cannot show
        # pressure that arrived mid-run.
        "memory_suspect_keys": [r["key"] for r in rows if r.get("memory_suspect")],
        "min_free_gb_any_task": min(
            [r["min_free_gb"] for r in rows
             if r.get("min_free_gb") is not None] or [None]),
        "order_seed": args.order_seed,
        "task_order_randomised": True,
        "worker_reloads": reloads,
        "worker_reload_count": len(reloads),
        "worker_reload_cost_s": round(sum(x["cost_s"] for x in reloads), 1),
        "refresh_commit_ratio_threshold": REFRESH_COMMIT_RATIO,
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
        # Two different numbers, and conflating them understates the cost of
        # a resumed run: this process only ran the tasks it did not resume.
        "wall_s_this_session": round(time.time() - started, 1),
        "wall_s_all_runs": round(
            sum(x["wall_s"] for runs in results.values() for x in runs), 1),
        "status_counts": status_counts,
        "unmeasured": len(unmeasured),
        "unmeasured_keys": [r["key"] for r in unmeasured],
        "partially_unmeasured_keys": unmeasured_keys,
        # S1 - never collapsed. A reader must be able to see how much of the
        # pass rate came from the final sweep rather than from the model
        # announcing completion.
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
        # An EXCLUDED task contributes 0 runs, so a tier whose tasks were all
        # excluded divides by zero here. The report block above already
        # guarded this; the printing block did not, and it crashed AFTER the
        # numbers were computed - on a 2.5-hour run that means losing the
        # summary at the very end. Report the tier as unmeasured rather than
        # inventing a rate for it.
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
    lock.release()
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
