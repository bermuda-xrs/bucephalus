# Release checklist — what "first iteration complete" means

This project's deliverable is **a measurement, plus an instrument honest
enough that the measurement can be checked**. So "done" is not "the code
runs"; the code has run for weeks. Done is: a number, with its noise floor,
its calibration, its profile, and its caveats attached.

Nothing below is a formality. Each line is something that has actually gone
wrong here at least once.

---

## 1. The instrument

- [x] `setup.ps1` completes and the suite is green (223 tests)
- [x] `demo.py` runs end to end with no network, refuses an injected
      instruction, and reports a verifying hash chain
- [x] Every benchmark answer independently recomputed — no expected value
      exists only as a table entry (`tests/test_bench_suite.py`)
- [x] Every verifier rejects a deliberately wrong answer. A suite whose
      checks cannot fail measures nothing; an audit found this in ~59% of
      SWE-bench Verified's hardest tasks
- [x] Every arm of every run starts from identical bytes on disk
      (`src/agent/workspace.py`, invariant 28)
- [x] Long runs are resumable and refuse to blend across a changed suite,
      split, or profile
- [x] The gate's binding conditions are pinned by tests, not just comments
      (`min_discordant_pairs` guards; `min_lift_pp` is inert below n=160)
- [x] Both long paths dry-run tested on scripted outcomes before spending
      hours (`tests/test_calibration_path.py`)

## 2. The measurement

- [ ] **Noise floor**, all 30 tasks × 3 baseline runs. Classify each task
      3/3, 0/3, or flaky. **More than 8 of 30 flaky → stop and tighten task
      specs**; a lift measured against that much noise cannot be interpreted
- [ ] **Calibration**, the hand-written skill through the full gate on all 30
      as a positive control. **If it is rejected, the instrument is broken
      and every later rejection is uninterpretable.** A gate that rejects
      everything and a gate that works are indistinguishable until this
      passes
- [ ] Run sheet generated (`bench/report.py`) with profile, models, date and
      caveats attached to the numbers rather than footnoted

## 3. The claim

State it in this shape, or not at all:

> Skills that pass a paired-trial gate raise a local model's task success by
> **X points** at **Y%** of the cost of a frontier model, measured on **N**
> tasks, on **DATE**, model **VERSION**, profile **local**.

- [~] Reported three ways — the run sheet renders the table and **names the bare-model arm as never measured**, which bounds the claim to "a skill moves the scaffolded model". Running that arm is still outstanding
- [x] Contamination caveats in the **same paragraph** as the number — enforced by `bench/report.py` and asserted by a test that scans the rendered line
- [x] Anything unverified is called unverified. The Anthropic adapter's module docstring carries `UNVERIFIED ... NEVER been exercised`

## 4. Packaging — known state

- [x] A wheel builds at this path. The Greek-character problem affects only
      *editable* installs, which write a `.pth` in the system ANSI codepage
- [ ] **The wheel is not runnable standalone.** It carries all 64 modules and
      none of `policy.md`, `themes/`, `assets/`, or the config files, which
      the runtime resolves relative to the config's parent. Today this is a
      clone-and-run-from-the-directory project. Making it installable means
      shipping those as package data with a resource loader, or an
      `alpha init` that materializes them into a working directory
- [ ] **No LICENSE.** Deliberate — that is the author's decision, and an
      unlicensed repository is "all rights reserved" by default, which may
      or may not be what is wanted
- [x] `CHANGELOG.md` reflects what actually changed, including what is still
      undone

## 5. Before tagging

- [ ] `git log --oneline` reads as the project's memory — no commit message
      claims more than was verified
- [ ] `HANDOVER.md` §8 (the honest list) matches reality
- [ ] `runs/` artifacts worth keeping copied into `docs/evidence/`
- [x] The version in `pyproject.toml` and `src/agent/__init__.py` agree (both 0.1.0)

## 6. Running a measurement, operationally

Learned the hard way, each after it went wrong:

- Drive every long run through `.\measure.ps1 next`. It runs detached
  (session teardown kills foreground jobs mid-run), refuses to start while
  another holds the lock, and refuses to chain past a decision gate.
- **Never start a second measurement.** Two overlapping passes measured three
  tasks under 2x CPU load, and `budget_exhausted` is a time limit, so
  contention can manufacture that outcome. `src/agent/runlock.py` now makes
  this impossible rather than discouraged.
- Nothing else on the machine during a pass. The same task takes 162s idle
  and 339-360s with a test suite alongside.
- `work/` sits under OneDrive, whose sync client holds handles. The workspace
  reset deletes files rather than directories because of it. Excluding
  `work/` from sync would remove the whole class of problem.
- `.\measure.ps1 status` is the source of truth for what may run next; the
  gates live in `bench/nextstep.py`, not in anyone's memory.

---

## The rule that outranks the rest

If a result is disappointing, publish it anyway with the caveats intact. The
literature says self-generated skills average about **−1 point**. The gate
exists so that failing is *visible* rather than silent, and a believable
number with its flaws disclosed beats an impressive one nobody can check.
