# Evidence

`runs/` is git-ignored — it fills with churn from every execution. These are
the run records worth keeping, copied here so the repository carries the
measurements its claims rest on.

| File | What it is |
|---|---|
| `smoke6.json` | Baseline arm, 3 easy drafting tasks, no skills. **0/3.** |
| `sighting-with-skill.json` | Same 3 tasks with the calibration skill. **3/3.** |
| `pareto-3b.json` / `pareto-8b.json` | Early 4-task model comparison, before the aero_calc suite existed. Superseded; kept because it is where the "8B is dominated on both axes" finding came from. |
| `bench-local.json` | First harness run that completed end to end. |
| `noise-floor.json` | Present only once the full noise floor has been run. |

## The one result so far

Three easy drafting-split tasks, `granite4.1:3b`, `profile=local`, one run
each, deputy out of the loop:

| task | baseline | with skill | produced | expected |
|---|---|---|---|---|
| E01 | fail | **pass** | 7672.598 | 7672.6 |
| E02 | fail | **pass** | 95.502 | 95.5 |
| E03 | fail | **pass** | 5027.047 | 5027.0 |

0% → 100%, 3/3 discordant, all favouring the skill. Mean wall clock 210s →
109s, because the baseline burns turns on wrong answers it then retries.

E03 is the clearest evidence the suite measures what it was built to measure.
Bare, the model answered 3666.7 — which is `sqrt(2 * mu_mars / R_earth)`, the
documented "Earth constants on a Mars problem" trap, hit exactly. With module
2 of the skill it read the right body's radius.

**This is a sighting shot, not a result.** n=3, single run, easy tier only,
drafting split. What it settles is that the instrument responds to a real
skill, so the full paired gate is worth its hours. It is not evidence of a
lift and must never be reported as one.
