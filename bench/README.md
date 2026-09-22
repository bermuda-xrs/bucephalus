# bench/ — this project's own instrument

**Everything here is the developer benchmark. None of it ships.**

`bench/` is how this project measures itself: the G1 mechanism ablation, the
skill gate's paired trials, the noise floor, the harness-tax ladder. It is not
a suite bundled for users, and it is not a general benchmark. The four tasks
in `bench/tasks/` are **examples of the shape**, and its own README says so.

## The product ships empty, on purpose

`--suite` is a required argument with no default and no fallback. That is the
boundary, and it is deliberate:

* **A suite is the denominator of every number the system reports.** A bundled
  default would mean people quoting pass rates for work they do not do. The
  only suite that can measure anything useful about your workload is the one
  you write for it.
* **A shipped task is a shipped ANSWER.** Every `expected_value` task carries
  `x-expected` in its file, and `contains_all` carries `x-contains`. Those are
  grading keys; they belong beside the grader, not in site-packages.
* `--suite` lost its default when the aerospace suite was archived, and a
  command copied from an older document now exits 2 rather than silently
  measuring something else.

`bench/` sits outside `src/`, so `[tool.setuptools.packages.find]
where = ["src"]` cannot see it. That exclusion is structural, which is not the
same as decided — `tests/test_the_wheel_ships_no_tasks.py` builds a real wheel
and fails if any task content, or any grading key, reaches it.

## Writing your own suite

`bench/tasks/README.md` has the task format and the three rules that decide
whether a suite is worth anything. Two of them matter more than the rest:
`success_criterion` must name a real verifier and never `always_pass`, and the
suite must include tasks the thing under test should NOT help with.

## What is here

| | |
|---|---|
| `run.py` | the arms and paired-trial harness. `arms` for cost-vs-pass-rate, `paired` for the skill gate |
| `g1_ablation_run.py` | the G1 comparison: one arm per mechanism removed, control first |
| `noise.py` | the noise floor. ~2.5 hours, takes the machine |
| `ladder.py` | the harness-tax ladder, R0-R5 |
| `inventory.py` | FINDINGS 1.25 as a query: what has ever run for real |
| `report.py`, `coverage.py`, `nextstep.py` | read-only reporting over the record |
| `measurement.py` | `open_measurement`, which makes isolation a declared value rather than a default |
| `tasks/` | four example tasks and the format they follow |
| `suites/archive/`, `tasks/archive/` | the retired aerospace suite. Every published result remains reproducible from it; see FINDINGS 1.13 for why it was retired |

**Never run anything else during a timed run.** Wall clock is this project's
cost axis and contention corrupts it — the suite alone took 8 minutes under
contention against 1.5 — and contention can manufacture a `budget_exhausted`
on a task that would have finished. One measurement process at a time,
enforced by an OS lock (`src/agent/runlock.py`).
