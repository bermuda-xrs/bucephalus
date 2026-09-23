"""G1's ablation comparison: the same suite, once per mechanism removed.

The switches landed 2026-09-17 (config.MechanismsConfig). This is the run they
exist for: one CONTROL arm with the shipped system, then one arm per mechanism
with that mechanism absent and nothing else changed. Token counts come from the
model server's own accounting, so no API key is involved.

    python bench/g1_ablation_run.py --suite bench/tasks

WHAT THIS CAN AND CANNOT SUPPORT, in the same paragraph as the instruction to
run it. `bench/tasks` holds FIVE tasks, and its own README says the suite that
matters is one you write for your domain, aiming at thirty. Five tasks cannot
carry G1's ">=10x reduction in paid tokens": at n=4 the per-task variance
swamps the effect, and most of them are single-shot enough that one
budget_exhausted moves every number. What five tasks CAN do is show that each
arm actually runs, that the mechanism is actually absent in it, and roughly
where the token deltas sit. Read the output as a smoke test of the instrument,
not as G1.

Every arm writes into ONE fresh database under the output directory, never
`agent.local.db`: that record was produced under HARNESS_VERSION 2 and these
arms run under a later one, so mixing them is the exact confusion a bump
exists to prevent.

The grader is HELD OUT in every arm: `bench/run.py arms` opens its context
through `open_measurement`, which pins `grader_held_out`, so a failed answer
is told it failed and nothing else (FINDINGS 1.33).

Arms run SEQUENTIALLY and nothing else may run beside them. Wall clock is this
project's cost axis, contention corrupts it, and contention can manufacture a
`budget_exhausted` on a task that would have finished.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE_STEP = 2   # see DEPARTURES, and FINDINGS 1.32

# Every way the measured arms differ from config.local.yaml, by setting, with
# why. Applied by `write_configs` and written into summary.json, so what the
# comparison ran under is read off the record rather than recalled.
DEPARTURES = {
    # 1. THE DEPUTY IS OFF. A nondeterministic LLM in the measurement path,
    #    and these arms differ by one mechanism only. `cmd_paired` already
    #    turns it off for the same reason, and config.local.yaml's own comment
    #    says to for benchmark runs. The code gate still runs.
    "autonomy.auto_review": False,
    "autonomy.approve_scoped_writes": True,
    # 2. THE SYNTHETIC COMPACTION BOUNDARY. The real ceiling is essentially
    #    never reached on this executor - four pilot designs failed to trigger
    #    it (FINDINGS 1.32) - so waiting measures the model's reluctance to
    #    read rather than the mechanism.
    "compaction_probe_step": PROBE_STEP,
    # 3. THE SEMANTIC CACHE IS ON. config.local.yaml turns it off - no local
    #    tier, empty allowlist, a threshold of 100000 - with a comment saying
    #    compressing the executor's input on the executor is pure overhead. A
    #    defensible shipping choice that also makes the cache arm measure
    #    nothing. What this measures is what the cache WOULD save; what the
    #    shipped local profile saves is zero, by configuration.
    "active_local_tier": "local",
    "local_allowlist": ["compress_tool_output"],
    "compress_observations_over_tokens": 1200,   # the code's own default
}

# The control comes FIRST, deliberately: if the shipped system cannot complete
# this suite today, no ablation number below it means anything, and the run
# should be stopped rather than interpreted.
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
    # Bookkeeping, not a departure: one database for the whole comparison,
    # and NOT the 283-task record.
    raw["db_path"] = str((out / "agent.g1.db").resolve())
    raw["anchor_path"] = str((out / "anchor" / "chain.g1.anchor").resolve())

    # THREE DELIBERATE DEPARTURES FROM config.local.yaml - six settings, three
    # purposes - identical in every arm so they cancel. Named here AND written
    # into summary.json by `main`, because a measured config that
    # differs from the shipped one and does not say so is the profile defect
    # this project already has a control for. (This comment said "three" and
    # numbered only two for one run: the deputy lived in a separate block and
    # was recalled from memory instead of diffed. The record now diffs.)
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
            # STOP. An ablation number is a comparison against the control, so
            # a control that did not run leaves nothing to compare against -
            # and four more hours would produce numbers nobody can read.
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
