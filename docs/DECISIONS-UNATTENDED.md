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
