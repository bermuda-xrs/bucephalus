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
