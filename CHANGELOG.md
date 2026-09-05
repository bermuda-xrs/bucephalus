# Changelog

> **The aerospace suite is archived.** As of 2026-09-05 `aero_calc` lives
> under `bench/suites/archive/`, `bench/tasks/archive/` and
> `bench/archive/decompose_aero.py`, and is no longer any tool's default —
> `--suite` and `--tasks` are now required arguments. Every result below
> remains reproducible from the archive. Nothing was deleted. See FINDINGS
> 1.13 for why it was retired.

Dates are commit dates. `0.1.0` is not released yet — see "Unreleased" for
what still stands between here and a first iteration.

## Unreleased — Phase 1: the tax measured, v3 rejected

`HARNESS_VERSION` remains **2**. v3 was evaluated and rejected on evidence;
nothing in this section changes what counts as a pass, so every v2 number
still stands.

### Measured

* **The harness tax does not generalize — 1 of 3 tasks.** Ladder R0/R1 on the
  3B: E03 unaffected (3/3 → 3/3), E04 collapses (3/3 → 0/3), E05 partial
  (3/3 → 2/3). The premise that a one-line prompt change would "substantially
  raise every 3B number" does not hold.
* **The mechanism was wrong and is now narrower and better evidenced.** It is
  **answer position**, not chain-of-thought suppression. Failing responses
  emit `key: value` *before* any working; passing responses emit the number
  *last*. E04's failure states `delta_v_m_s: 8860`, then writes the correct
  formula and the correct substitution and never evaluates them — post-hoc
  rationalisation anchored to a token autoregression cannot revise. Length is
  downstream: a response that commits to a number first has nothing left to
  write. **E05 is the within-task control** — same task, same rung, same
  prompt bytes, temperature 0, both behaviours observed, the answer-first
  sample wrong and the two answer-last samples right.
* **The tax is capacity-dependent.** `granite4.1:8b` scores **5/5** at R1
  where the 3B scores 0/5, on the identical 456-character prompt. Recorded as
  a capacity threshold. It costs 77–127 s against the 3B's 7.7 s — 10–16× the
  wall clock, on a benchmark whose only cost axis is seconds. Its shown
  working is not a faithful trace: it writes `sqrt(58873.6) = 242.6 m/s`, then
  "approximately 7.6 km/s", then lands correctly on 7663.2.

### Decided

* **v3 is not applied.** R4 0/5, R5 0/5 with the step-by-step instruction
  appended to the suite goal — the injection point the real change would use.
  Both decision routes agree: the fix does not survive the loop, and the tax
  it targets does not generalize. The "you would not want to ship a
  CoT-suppressing harness regardless of pass rates" argument was conditional
  on v3 surviving Phase 1, and it did not.
* **What v3 *did* do is recorded as a real effect that is not a pass.** It
  improved error **structure** 3.6× on the monotonic scorer with **no** change
  in pass rate: 11,185,784.5 (145689% out, score 0.077) became
  7909.544108195981 (3.09% out, score 0.279). That figure is
  `sqrt(mu/(6371000+400))` exactly — the model reads `altitude_km: 400` and
  adds **400 metres**, the suite's own km-to-m trap. By FINDINGS §4's
  criterion this makes the suite measure what it was built to measure. It
  still fails, and it is not tuned into a pass.

### Found — a fourth bug in each documented class

* **Instrument (4th): the ladder's R4 rung scores step allocation, not
  correctness.** R4 sets `max_steps=1`, so it measures *whether the model
  spent its one action on `run_code`*. Four of five samples spent it on
  `read_file(params.json)` — correct behaviour — and scored zero in 7.4 s. The
  bias is directional: "work through it step by step" is an instruction to
  take preparatory steps, exactly what R4 punishes. The published v2 R4 row
  carries the same defect. Found by applying the documented test — what would
  this check score for an agent that did the work perfectly but behaved
  slightly unusually? See SPEC-DELTA C16.
* **Process (4th): `reap_orphans()` kills healthy workers.** It matches on
  process name alone and `kill_tree()`s every `llama-server`, live or not;
  it killed the warm model three times during Phase 1, defeating
  `keep_alive`. This is the mirror image of the documented pattern — not
  "anything spawned outlives what spawned it" but "the reaper cannot tell a
  live child from an orphan". Because R0 runs first, R0's first sample always
  absorbs a cold load, so the published `45.7 s → 7.7 s` drop is partly
  cold-load and is not a clean effect size; the drop survives on warm samples
  (E04: 24.3 s → 12.0 s). `noise.py` calls the reaper once in preflight, not
  per task, so full-suite medians are unaffected. See SPEC-DELTA C17.
* **Temperature 0 is not byte-deterministic here.** Four counterexamples
  across both models on identical prompt bytes. The pattern is specific: the
  *first* sample of a block diverges, subsequent samples are byte-identical.
  E05's is a correctness flip, not a cosmetic one. Any plan that cuts repeat
  passes because "warm runs at temperature 0 are byte-identical" rests on a
  false premise; cold-start repeats are the right variance instrument. See
  SPEC-DELTA C18.
* **The 8B does not fit this machine alongside normal desktop use.** Measured:
  7.64 GB resident at the executor's `num_ctx: 16384`, against 3.6–6.4 GB free
  depending on what else is open — leaving 0.79 GB or less against a 2 GB
  house-rule floor. One Phase 1 model call took **2465 s** against a 600 s
  budget and manufactured a `budget_exhausted`; since that outcome is a time
  limit, memory pressure converts directly into apparent task difficulty. See
  SPEC-DELTA C20.

### Changed

* `bench/ladder.py` gains `--step-by-step`, which appends the reasoning
  instruction to the contract at R0–R3 and to the **suite goal** at R4/R5 —
  the goal deliberately, because that is where the real v3 change would land.
  The instruction is a named constant echoed into every run record; the
  earlier probe preserved its result but not its prompt.
* Every ladder run record now carries `harness_version`, `step_by_step`,
  `step_by_step_text` and `rungs`.
* **The two `HANDOVER.md` files are one.** `docs/HANDOVER.md` absorbs the root
  file's architecture map, module map, verifier table, traps, glossary and
  "the claim to actually make" as sections 17–21; the root file is deleted and
  all pointers repointed. Seven stale claims were dropped rather than merged
  and are listed in section 17 — six are ordinary drift, and one is
  **withdrawn rather than stale**: "3 tasks, 0/3 baseline → 3/3 with the
  calibration skill" predates the shared-workspace defect (C12) that
  manufactures exactly that result.
* `--rungs` is documented, along with every other ladder flag, including why a
  rung subset is a valid experiment and why the R3/R4 seam is not a
  like-for-like step.
* The documented venv path was wrong for a OneDrive checkout (`..\..\..\`
  lands on `OneDrive`, not the user profile). Now `%USERPROFILE%`.
* `cal.json` deleted — a tracked artifact of an aborted calibration whose own
  verdict was "insufficient / stub".

## Unreleased — the measurement

Everything in this section is instrument work. The system was already running
end to end; what was missing was the ability to trust a number it produced.

### Fixed

* **H30b was measuring which convention the model guessed, not physics.**
  The task never said which heliocentric speed to use, and its two sources
  disagree: `constants.json` gives `mars.orbital_v = 24077` (Mars's true mean
  speed, an orbit with e = 0.0934) while `sqrt(mu_sun/r2)` = 24131.5 (a
  circular orbit at r2). Both are correct facts about different models; the
  0.23% gap amplifies to 1.27% in the capture burn. Fixed by pinning the
  convention in the objective, **not** by touching the tolerance. A
  spec-following answer now scores 0.9995 through the real sandboxed
  verifier, where it previously scored 0.37 — which would have made the M7
  ratchet prefer noise over right answers.
* **`bench/run.py` never enforced a backend profile.** `Config.profile`
  defaults to `mixed` and `config.local.yaml` does not set the key, so the
  gate run — the most important measurement in the project — was the one
  command not checking what it was allowed to touch, and would have recorded
  `mixed` on a run that was in fact local.
* **The noise floor could not survive being killed.** Every result was held
  in memory and written once, after the final task, so a kill at hour three
  of a four-hour pass lost all of it. Results now append to a
  `.partial.jsonl` ledger, fsynced per row, with a signature-checked
  `--resume`.
* **A resumed report understated its own cost** — it counted only the tasks
  that process ran. Split into `wall_s_this_session` and `wall_s_all_runs`.

### Added

* `--split` on `bench/run.py paired`, so the contamination boundary is
  expressible. `check_contamination` only fires on a task named in a skill's
  provenance, and a hand-written skill names none.
* **Two-number calibration reporting.** `evaluate_skill` takes a `task_keys`
  subset, so the contamination-clean eval analysis is read off the *same*
  stored trials as the full-suite analysis — no extra compute. PRIMARY (all
  tasks, positive control) and SECONDARY (eval subset, underpowered) are both
  printed and written to `runs/calibration.json`, each carrying its caveat as
  a field on the number rather than a footnote.
* Every gate verdict now carries the full stat block — `n`, `b`, `c`,
  `discordant`, `lift_pp`, `p` — including `insufficient`, which previously
  reported only `n` and so hid which way an underpowered result pointed.
* `paired --resume`, skipping pairs with **both** arms recorded. A
  half-finished pair is deliberately re-run, because the analysis drops any
  pair missing an arm.
* `status_counts`, `ended_at_step_limit`, `ended_budget_exhausted` and
  `tier_pass_rates` on the noise report, so a run records *why* it ended.
  `max_steps` means the agent burned all six turns; `plateau` means more
  turns bought nothing; `budget_exhausted` means it ran out of **time**, and
  may not have been failed on the physics at all.

  **Corrected same-day:** this was first documented as "`budget_exhausted`
  has never fired and largely cannot", on the reasoning that `max_steps: 6`
  binds before the 600s budget. M16 then ended `budget_exhausted` at 705.6s
  in pass 1 — six slow turns on a medium task cross 600s of model time before
  the step limit. Both limits bind, and which one does is itself a signal.
* `tests/test_calibration_path.py` — the hours-long calibration driven end to
  end on scripted outcomes, including the case where the skill **hurts**.
* `tests/test_noise_durability.py` — including a real subprocess killed
  mid-pass.
* `tests/test_bench_suite.py` — the suite's own answers recomputed by an
  independent solver, and a check that all 30 verifiers reject a deliberately
  wrong answer.

### Changed

* `gate.min_paired_trials` 30 → 15. **This is not a weakening.** McNemar
  reads only discordant pairs, so n=15 with 10 discordant is exactly as valid
  as n=30 with 10 discordant. Holding `min_discordant_pairs` at 10 against a
  pool of 15 rather than 30 raises the bar from 33% of outcomes changed to
  67%. `min_discordant_pairs` is the load-bearing guard and must not be
  lowered; `min_lift_pp` is **inert** below n=160 and is not protection.
* Invariants 26–28 added, covering measurement-run durability and task-level
  trial counting.

### Known, and not yet done

* The noise floor has been run to **pass 1 of 3** only.
* The calibration skill has **never** been through the full gate on real
  models. The path is dry-run tested; it has not produced a real verdict.
* **No generated skill** has been produced or evaluated.
* The **Anthropic adapter is unverified** — written and unit-tested, never
  exercised against the live API. Do not describe it as working.
* The built wheel is **code-complete but not runnable standalone**: it
  carries all 64 modules and none of `policy.md`, `themes/`, `assets/`, or
  the config files, which the runtime resolves relative to the config's
  parent. Today the project is "clone and run from the directory", not "pip
  install and go".
* No `LICENSE`. That is a decision for the author, not a default.

## 0.1.0 groundwork — 2026-08-17 → 2026-08-24

* M1–M13 implemented from `spec/BUILD-SPEC.md` with its own acceptance tests.
* Sources restored twice after OneDrive data loss; the virtualenv now lives
  outside the sync folder.
* Runnable with no API key at all, on Granite 4.1 through a dedicated Ollama
  instance on port 11435.
* Control plane hardened — bearer token, Origin allow-list, DNS-rebinding
  protection — before being exposed anywhere.
* User-authored suites, policy and grounding moved out of files into a web
  console.
* **Seven components that had passing unit tests and zero call sites were
  wired into the runtime.** A test that a function works is not a test that
  the system calls it; `tests/test_wired_invariants.py` exists because of it.
* The 30-task `aero_calc` suite, generated from one table, with a
  drafting/eval split and independently recomputed answers.
* Enforced backend profiles: `--profile local` refuses cloud backends *and*
  provider credentials, and the profile is recorded on every run.
* `spec/CORRECTIONS.md` — errata for the original specification, which
  contained a model that does not exist, the wrong tokenizer, an API
  parameter that now returns 400, an inverted comparison contradicting its
  own test, and a config that trips its own assertion.
