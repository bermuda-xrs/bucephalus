# Decisions made on the user's behalf - iteration 3 (unattended, from 2026-09-28)

Each entry: the question, the options, the choice, why, and how to reverse it.
"WAITING FOR USER" marks a choice that was irreversible and ambiguous, so it
was not made.

## U1 - Which state leaves a checkout that sits in a synced folder (1.1)

- **Question.** "State must never live inside a synced folder." Which state?
- **Options.** (a) move the whole state root to platformdirs; (b) move only
  runtime state: database, anchor, `runs/` records the product writes; keep
  tracked content (`grading/`, `skills_store/`) and the task workspace `work/`.
- **Chosen: (b).** (a) strands the 30 tracked graders and 12 seed skills, and
  breaks `bench/` from a checkout. `work/` holds the user's own task output.
  The database (every goal, answer and tool output) and the anchor that
  authenticates it are what must not be uploaded.
- **Existing files are not moved.** `agent.db`, `agent.local.db` (the
  283-task measurement record) and `anchor/*.anchor` stay in the checkout and
  stay synced until the user moves them; a one-time notice names them. Moving
  a measurement record is not a call to make unattended.
- **Consequence to know about.** `alpha` run from this checkout now starts on
  an empty database in `%LOCALAPPDATA%\alpha-test-1\alpha-test-1\checkouts\
  alpha-test-1-<hash>\`. The desktop app is unaffected (it always used
  platformdirs).
- **Reverse.** Set `ALPHA_DATA_DIR` to the checkout, or copy the old database
  into the new directory. Code: `config._synced_state_dir`.

## U2 - The operator's real memory holds test content: WAITING FOR USER

`%LOCALAPPDATA%\alpha-test-1\alpha-test-1\memory\MEMORY.md` contains
`- [user] injected instruction`, written 2026-09-25 by the audit's probe (the
suite did not isolate the user data dir; fixed in 38edfa1). It is in the
prefix of every real session since. The run's rules forbid touching real
memory files, so it is left. To remove it:
`alpha memory --forget "injected instruction"` (then `alpha memory` to check).

## U3 - Memory integrity is not compared against the chain (1.4)

- **Asked.** Compare tier-1 memory against the `memory.written` event on the
  chain, "the authenticated record".
- **Measured (Phase E, D44; re-read 2026-09-28).** Memory is ONE store per
  user, shared by every config; each config's chain is its own database, and
  `alpha memory --accept` blesses a state with no chain event. A load-time
  comparison alarms on another config's legitimate write and on every
  accepted state (`tests/test_memory_integrity_claim.py` went red under the
  naive wiring). The chain is also not a stronger authenticator here: it and
  `.fingerprint` are both local files the out-of-band writer can rewrite.
- **Chosen.** Keep E5's correction: the chain is audit evidence, stated as
  such. Nothing changed.
- **Reverse / revisit.** A per-user memory journal with its own chain would
  remove the width problem but not the local-writer one; worth it only with
  a secret the writer cannot read (an OS keystore), which is a new
  dependency - a user decision.

## U4 - No "slow" marker; the default run is the whole suite, in parallel (2.2)

- **Asked.** Mark genuinely slow tests so a fast default run exists, with the
  full run one flag away.
- **Measured.** In parallel (`-n auto --dist loadgroup`, 14 cores) the WHOLE
  suite takes 46.7-47.5 s. No test takes more than 14 s (the wheel build's
  setup); the frozen builds and real-model tests are not in the suite at all
  (the frozen gate is `packaging/build.py --probe`; live models skip without
  credentials). A fast subset would save seconds and create a second, weaker
  definition of "the tests pass".
- **Chosen.** One default: everything, parallel. `-n 0` for serial.
- **Reverse.** Add `markers = ["slow: ..."]` and `-m "not slow"` to addopts.

## U5 - Mutation testing with a small in-repo runner, not mutmut (2.3)

- **Why.** mutmut does not support Windows; cosmic-ray needs a job database
  and config per module. `scripts/mutate.py` (~200 lines, its own test) does
  one AST operator per mutant in a private copy of src/, never the repo.
- **Known bias.** Tests that spawn `python -m agent...` run the real source,
  so a mutant only they would catch is reported as SURVIVING. Every survivor
  was re-checked before a test was written for it.

## U6 - The console's design direction (4.1)

- **Question.** Hermes' layout was pinned; the look was not.
- **Chosen.** Your theme's own palette (themes/default.toml: #0E1116 ground,
  steel-cyan accent), amber kept for "needs you", the integrity red for
  integrity only; the system sans for text and a monospace only for data
  (hashes, paths, tool arguments). One bold element: the plan card as a
  document you sign - the objective as its headline, what to settle first in
  an amber block, a numbered track (a real sequence) with irreversible steps
  ringed, the hash in a seal that stamps when signed (the one animation;
  reduced-motion respected). WCAG AA measured for every text/background pair,
  both themes (lowest 5.39:1). No web fonts: the CSP allows none from
  elsewhere and bundling one adds weight for no information.
- **Reverse.** ui.css tokens on :root; the plan card is `.plan` and `.seal`.

## U7 - No minimize-to-tray (4.6): WAITING FOR USER, as proposal P4

A tray icon needs a second library (pywebview has none; pystray is the usual
one) and a process that outlives its window - against the desktop app's
rule that closing the window stops everything, which is what the no-orphans
test holds. Not built. `docs/PROPOSALS.md` P4 says what it would take.

## U8 - Two audit mediums fixed rather than deferred (5.4)

- **The rule.** Critical and high are fixed; medium and low go to DEFERRED.
- **What I did.** Fixed S4 (D71) and I2 (D73) anyway, and deferred the other
  mediums (S3 hard links, I1 poll cost) and all lows.
- **Why.** Both are REGRESSIONS this run introduced - S4 by moving state into
  the installed mode's root (1.1), I2 by the per-file check of the D61 fix -
  and both fixes are small and red-first. Leaving a cross-context database
  read that this run created, on the grounds that it rated medium, would hand
  the user a regression rather than a backlog item. The mutation runner's B1/B2
  were fixed for a different reason: they corrupt numbers this report states.
- **Reverse.** Revert the S4 line in `PROJECT_STATE` and the I2 caching in
  `security/capabilities.py` / `control/files.py` / `execute/tools.py`.

## U9 - D79, a graded program that prints its own verdict: WAITING FOR USER, as proposal P5

- **Found** killing the verifier survivors of 2.3: `verify_program` reads the
  first verdict marker on stdout, and the grader runs the graded code in its
  own interpreter, so that code can print a pass. Shown by a strict xfail.
- **Not fixed.** Every fix (the last marker, a private channel, a child
  process for the answer, exact-type comparison in graders) changes what
  counts as a pass - HARNESS_VERSION, which this run may not bump - and the
  last two change the graders, which are part of the measurement. No
  recorded run shows it: the model sees neither the harness nor the grader.
- **Reverse.** Nothing to reverse; P5 lays out the three options.

## U10 - The suite's wall time is recorded as machine state, not a regression (2.2)

- **Measured.** 2.2 recorded 47 s parallel on 2026-09-28. On 2026-09-29 the
  suite took 180 s at HEAD with 6, 10 or 14 workers alike, and the 2.2-era
  commit (5d4b0b9) took 155 s in the same hour - so the machine, not the
  code: 4.7 GB of 15.5 GB free with 14.5 GB committed by processes that are
  not this project's, on AC power. The same test takes 1.2 s alone and 27 s
  in the loaded suite.
- **Chosen.** Report both numbers with their conditions; change nothing. A
  timing claim holds only against a run on the same machine state, which is
  why Part 3 times before and after back to back.

## U11 - The measurement record keeps the empty tables the suite added (D80)

- **What happened.** A pre-existing test opened `agent.local.db` read-write,
  so this run's schema reached the record: two empty tables and an index.
  No row changed; the chain verifies.
- **Options.** (a) drop them; (b) leave them.
- **Chosen: (b).** Dropping is another write to the record, the pre-change
  bytes are not recoverable from anything this run kept, and any reader
  that opens the record through `open_db` adds them back. Earlier schema
  additions since 2026-09-06 reached it the same way and were never noticed.
- **Reverse.** `DROP TABLE conversation_tasks; DROP TABLE conversations;`
  on the record, if the user wants the schema as of the last measurement.
