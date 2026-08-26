# Changelog

Dates are commit dates. `0.1.0` is not released yet — see "Unreleased" for
what still stands between here and a first iteration.

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
  `tier_pass_rates` on the noise report. `budget_exhausted` has never fired
  and largely cannot — `Budget.spent_seconds` accumulates model wall time
  only, and `max_steps: 6` binds long before 600s — so `max_steps` is the
  real "the agent is looping rather than the task being hard" signal.
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
