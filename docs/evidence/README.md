# Evidence

`runs/` is git-ignored — it fills with churn from every execution. These are
the run records worth keeping, copied here so the repository carries the
measurements its claims rest on.

| File | What it is |
|---|---|
| `smoke6.json` | Baseline arm, 3 easy drafting tasks, no skills, 2026-08-24. **0/3.** |
| `sighting-with-skill.json` | Same 3 tasks with the calibration skill, 2026-08-24. **3/3.** |
| `sighting-2026-08-26-baseline.json` | The same baseline re-run on 2026-08-26. **1/3** — see below. |
| `sighting-2026-08-26-skill.json` | The same skill arm re-run on 2026-08-26. **3/3.** |
| `pareto-3b.json` / `pareto-8b.json` | Early 4-task model comparison, before the aero_calc suite existed. Superseded; kept because it is where the "8B is dominated on both axes" finding came from. |
| `bench-local.json` | First harness run that completed end to end. |
| `noise-floor.json` | Present only once the full noise floor has been run. |

## The sighting shot, run twice, two days apart

Three easy drafting-split tasks, `granite4.1:3b`, `profile=local`, one run
each, deputy out of the loop.

| task | baseline 08-24 | baseline 08-26 | with skill (both dates) | expected |
|---|---|---|---|---|
| E01 | fail | fail | **pass** | 7672.6 |
| E02 | fail | fail | **pass** | 95.5 |
| E03 | fail | **pass** | **pass** | 5027.0 |
| | **0/3** | **1/3** | **3/3** | |

**The baseline did not reproduce, and that is the finding.** E03 passed bare
on the second date. It is not a regression: `bench/suites/aero_calc.py` was
byte-identical between the two runs — the only change since, H30's objective,
does not touch E03 — and the failure modes on both dates were the documented
traps, hit exactly:

* E01 bare answered **7909.79** = `sqrt(mu / R_earth)`, the "altitude is not
  orbital radius" trap.
* E03 bare on 08-24 answered **3666.7** = `sqrt(2 * mu_mars / R_earth)`, the
  "Earth constants on a Mars problem" trap.

So the suite measures what it was built to measure, and **E03 is flaky**: one
pass in three baseline observations. The terminal statuses moved too — E01 and
E02 each ended `max_steps` on one date and `plateau` on the other.

That is the whole argument for measuring a noise floor before claiming a lift,
and it is why "0/3 baseline" must never be quoted as a fixed baseline. It was
one sample.

**Both dates agree on the skill arm: 3/3.** Against the 08-26 baseline the
skill won 2 discordant pairs, none against; against 08-24, 3 and none.

**This is a sighting shot, not a result.** n=3, single run per arm, easy tier
only, drafting split. What it settles is that the instrument responds to a
real skill, so the full paired gate is worth its hours. It is not evidence of
a lift and must never be reported as one.

## Wall clock, and why durations here are only comparable within a run

Same task, same model, CPU-only:

| condition | E01 wall clock |
|---|---|
| machine otherwise idle | 162s |
| light contention | 186s |
| full pytest suite running alongside | 339–360s |

A task roughly doubles under CPU contention. Any duration in this directory is
comparable only against another measured the same way, and the noise floor is
run with nothing else on the machine.

## What CPU contention does to a measurement (2026-08-27)

Two `noise.py` passes overlapped for 36 minutes because a process that looked
dead was not. Three tasks were measured during the window, and the same tasks
were re-measured afterwards with the machine to themselves:

| task | contended | clean | |
|---|---|---|---|
| M16 | `budget_exhausted` 705.6s | `max_steps` 488.6s | different failure mode |
| H23 | `budget_exhausted` 931.3s | `plateau` 127.7s | **7.3x** wall clock |
| H24 | `budget_exhausted` 1152.1s | **`done`** 280.9s | **recorded a PASS as a failure** |

H24 is the one that matters. `budget_exhausted` is a **time** limit, so
contention did not merely slow the task down — it manufactured a failure on a
task the model solves correctly in 281 seconds. A noise floor built from that
window would have reported the suite as harder than it is, and the tasks it
mislabelled would have been the ones a skill is later asked to lift.

The contaminated rows were discarded, not corrected, and `src/agent/runlock.py`
now makes a second concurrent measurement impossible rather than discouraged.

**The general rule this is evidence for:** every wall-clock number in this
directory is only comparable to another measured under the same load, and any
outcome derived from a time limit is only as trustworthy as the machine was
quiet. The same easy task takes 162s idle and 339–360s with a test suite
running alongside.

---

# The noise floor, pass 1 of 3 (2026-08-27)

All 30 tasks, baseline arm, `granite4.1:3b`, `profile=local`, one run each.

| | |
|---|---|
| mean pass rate | **10.0%** (3/30) |
| easy | 20.0% (2/10) |
| medium | **0.0% (0/12)** |
| hard | 12.5% (1/8) |
| ended | plateau 14 · max_steps 11 · done 3 · budget_exhausted 2 |

`runs/noise-floor.json`, preserved here as `noise-floor-pass1.json`.

**This trips the difficulty gate** — 10% is far outside the 25–75% band that a
single pass is meant to catch gross miscalibration against. The gate's own
counter-argument is that the noise floor measures the *baseline arm only*, and
a low baseline with a working skill arm is near-maximum discordance, which is
ideal for McNemar. So the discriminator was run rather than rebalancing blind.

# The skill arm, all 30 tasks (2026-08-27)

Measured in three runs (`sighting-medium-hard`, `skill-arm-headroom`,
`skill-arm-rest`) so that every task has both arms.

| | baseline | with skill |
|---|---|---|
| easy | 2/10 | **7/10** |
| medium | **0/12** | **3/12** |
| hard | 1/8 | 2/8 |
| **all** | **3/30 (10%)** | **12/30 (40%)** |

Paired outcome:

| | n | tasks |
|---|---|---|
| **b** skill wins | **10** | E01 E02 E05 E06 E09 E10 H25 M11 M14 M17 |
| **c** control wins | **1** | **E08** |
| concordant pass | 2 | E03 H24 |
| concordant fail | 17 | |

**discordant 11 · lift 30.0pp · p = 0.00586**

Against the shipped gate: n≥15 ✓ · discordant≥10 ✓ · lift≥5pp ✓ · p<0.05 ✓.

## What this is, and what it is not

**It is not the gate's verdict.** These are two arms measured *separately* —
the baseline from the noise-floor pass, the skill arm from three later runs.
`run_paired_trial` runs both arms per task with randomized order in one
session, and writes `skill_trials`, which is what `evaluate_skill` reads.
Treat this as a well-grounded prediction that the calibration will pass, not
as the result.

**Single run per arm.** The noise floor is at pass 1 of 3, so per-task
flakiness is unmeasured. E03 has already passed 2 of 4 baseline observations,
so at least one of these 30 cells is known to be unstable. Some of the 11
discordant pairs could flip on a repeat.

**E08 is the interesting one.** It passes bare and fails with the skill — a
skill-caused regression on a task the skill's module 2 was written to help
(Moon surface gravity, "identify the body"). The literature reports human
written procedures hurting on 16 of 84 tasks; this is one. It is not flagged
`catastrophic` in the suite, so it costs a `c` rather than triggering a veto.

**The coverage heuristic under-counted.** `bench/coverage.py` marked E06 and
H25 as outside the skill's scope, and both lifted. The upper bound was
conservative in the useful direction, but it is a keyword match over a
one-line trap description, not a model of what the skill does.

---

## Superseded claims in this directory (added 2026-09-03)

The files themselves are left byte-identical, per the preservation rule. What
follows is what later evidence changed about how to read them.

### `b1-smoke-analysis.json` → `determinism_finding` is WRONG

It reads, in part: *"at temperature 0 with a warm cache, repeated runs in one
process are deterministic, so a noise floor measures almost no variance after
run 1 ... Passes 2-3 as configured would measure very little."*

That generalised correctly from B1's own five runs and **does not hold**.
Four counterexamples, on identical prompt bytes, across both models:

| observation | first sample | subsequent samples |
|---|---|---|
| E05 R1 (3B) | 362 ch, **wrong** (8775.75) | 685 ch, **right** (12918.75) |
| E04 R0 (3B) | 537 ch | 561 ch |
| E01 R1 (8B) | 7674.6, 1097 ch, 127.1 s | 7663.2, 1002 ch, ~78 s |
| E01 R5 (3B) | 7909.54 | 250.12, 250.12 |

The E05 case is a **correctness flip**, not a cosmetic one: same task, same
rung, same bytes, and it both passed and failed.

The mechanism the B1 note proposed is right — divergence is tied to the first
call after a model load. The conclusion drawn from it is not. Because the
first sample of every block is the divergent one, `n=1` per task inherits
exactly that variance, and the **first task of any run is the most exposed**.

**Consequence for planning:** repeat passes cannot be cut on the grounds that
warm runs are byte-identical. The correct variance instrument is **cold-start
repeats**, because cold start is the condition that produces the divergence.
See FINDINGS 1.11 and SPEC-DELTA C18.

### `ladder-E01-3b.json` → the R4 row is VOID, and it has no R5 rows

Two separate problems.

**R4 is void.** That rung ran with `max_steps=1`, which cannot measure "can
it deliver this" — it measures whether the model happened to spend its single
action on `run_code`. Of five samples in the v3 re-run, four spent their step
on `read_file(params.json)`, which is correct agent behaviour, and produced
no artifact in ~7.4 s. `bench/ladder.py` now runs R4 at `max_steps=3` and
keeps the one-action probe as `R4x`, named for what it is. See FINDINGS 1.9
and SPEC-DELTA C16.

**There are no R5 rows in this file at all.** The R5 line in
`ladder-E01-3b-analysis.json` is annotated `"real loop unrestricted (B1)"` —
it was carried across from the B1 run and presented in the same table as the
rungs that were actually measured here. When reading that table, R0–R3 and
R4 come from this file; R5 does not.

### `ladder-E01-3b.json` → wall-clock at R2 and R3 is cold-load inflated

`reap_orphans()` used to kill the live model between runs, so the first
sample of every rung absorbed a ~58 s cold load. Warm-only means differ
sharply at the later rungs:

| rung | published (sample 1) | warm-only mean |
|---|---|---|
| R0 | 45.7 s | 39.8 s |
| R1 | 7.7 s | 6.7 s |
| R2 | **14.3 s** | **1.5 s** |
| R3 | **13.9 s** | **2.8 s** |

**The headline ratio survives:** R0→R1 is 5.9× on both readings, so
"45.7 s → 7.7 s" overstates the absolute figures but not the effect. The
mechanism section's "45.7 → 7.7 → 1.4" is inconsistent, though — it takes
sample 1 for R0 and R1 and a warm sample for R2. Warm-only throughout it is
**39.8 → 6.7 → 1.5 → 2.8**. See FINDINGS 1.10 and SPEC-DELTA C17.

### `ladder-E01-3b.json` → the recorded `answer` is not always the model's answer

R1 records `answer: 6371000.0`. The model's stated answer was
`7.784152678`; 6371000 is Earth's radius, quoted in a trailing citation. The
extractor took the **last** number in the response.

No published verdict is wrong because of this — all 43 archived text-rung
samples were re-scored with the corrected extractor and **0 verdicts
flipped** — but the defect was live: a model that answers correctly and then
cites its inputs would have been scored wrong. The extractor now prefers a
number the model labelled with the output key, records `numbers_seen`,
`hits`, `labelled_answer` and `last_number`, and keeps the old verdict as
`correct_last_number` so the two readings cannot silently diverge.
