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

## U12 - Two efficiency targets reported as they stand (3.1, 3.2)

- **3.2, "shutdown under 2 s": not met, not chased further.** Measured by
  stage, the app's own work ends 0.3-0.4 s after WM_CLOSE (window hidden,
  children killed, database closed); the process object disappears at about
  2.5 s. Terminating self instead of `os._exit` measured no faster, so what is
  left is the OS tearing down the WebView2/CLR host. What was ours was cut
  (2.99 -> 2.63 s idle; 22.3 -> 3.4 s after a task, D81).
- **3.1, build size: measured, not cut.** 62 MB, dominated by sympy, which the
  sandbox offers model code; the TUI (textual) and pygments are ~7 MB of
  source the desktop app never runs, perhaps 2 MB compressed. Excluding them
  saves little and adds a build rule to keep true.
- **Reverse.** `packaging/build.py`: `--exclude-module textual`, and re-run
  the frozen gate and `frozen_e2e`-style checks.

## U13 - Mutation kills made only by load-sensitive tests count as survivors (2.3)

- **Measured.** The whole-suite recheck names the test behind each kill
  (D78). Some kills were made only by tests that fail under load whatever
  the code: the parallel-batch timing test, the crawl-delay test, and the two
  reaper tests (fixed 2026-09-29).
- **Chosen.** Such a kill is counted as a survivor, and the raw result files
  keep the runner's own status. The alternative - a clean re-run of each -
  would have cost hours of machine time for a handful of mutants whose
  classification is already clear (equivalent or dead, in the README).

# 2026-09-30, the continuation

## U14 - Agent code's process limit is the interpreter's own count (D49)

- **Options.** (a) add `_winapi.CreateProcess` to the audit hook's list; (b) make
  the kernel refuse: the Job Object's process cap for AGENT code (a run with
  protected paths) is exactly the interpreter's own processes.
- **Chosen: (b).** An audit-hook list is the whole class of bug D49 came from -
  each spawn primitive underneath the last one needs its own name, and ctypes
  has none. The kernel's limit holds for every route. Harness runs (graders
  running pytest) keep the normal cap: they must spawn.
- **Cost.** Agent code that legitimately runs a subprocess was already refused
  by the hook; nothing that worked stops working. Windows only - POSIX keeps
  its polled watchdog, with `os.fork` added to the hook's list.
- **Reverse.** `platform.windows.spawn_denied_processes` returns `None`.

## U15 - A POSIX orphan is "still in our session, adopter not" (D55)

- **Options.** (a) "parent is init or a subreaper"; (b) "parent's session differs
  from ours while the child's is ours".
- **Chosen: (b).** (a) selects every daemon - init adopts those too - and the
  reaper kills what it selects: a model server started as a service would go.
  A daemon starts its own session; an orphan of ours does not. A test starts a
  daemon in its own session and requires it never be selected.
- **Reverse.** `platform.posix.adopted` returns False (the reaper goes inert on
  POSIX again, as before).

## U16 - A crash inside a side effect is reported, never repeated (D27)

- **Options.** (a) at-least-once: re-run the effect on replay; (b) at-most-once:
  report `outcome_unknown` and let the model or the human check.
- **Chosen: (b).** A crash between a send and its receipt used to send twice;
  a duplicate push or message is worse than one that needs a look. The result
  says so in words the model can act on, and the receipt row stays claimed.
- **Reverse.** In `receipts.execute_once`, treat a claim with no in-process
  runner as absent (delete it and run) - one line, and the old behaviour.

## U17 - Five registered event types removed rather than emitted (D63)

- **Chosen: remove.** `step.started`, `step.completed`, `artifact.tombstoned`,
  `skill.promoted`, `skill.demoted` were registered and recorded by nothing, in
  any of the project's records. Emitting them would add chain events - bytes
  in every future record - for a vocabulary no client uses (progress rides
  `live.step`). A reverse test now holds the registry to the code.
- **Reverse.** Re-add the strings to `EVENT_TYPES`/`CLIENT_EVENTS` and emit them.

## U18 - The token estimator measured, not changed (D6)

- **Measured** from records already on disk: it undercounts by ~26%
  (server = 1.108 x est + 193 tokens, sd 23.5, n=180, granite via Ollama).
- **Not corrected:** the ceiling fires ~15% late, and fixing it changes when
  compaction fires - prompt bytes, a HARNESS_VERSION matter. PROPOSALS P6 has
  the change and the cheaper alternative (scale `context_fill_ratio`).

## U19 - The console's integrity cache is bounded and content-keyed (D72)

- **What happened.** A commit-time security review of the first version found
  its key too weak (size+mtime of the anchor; `data_version` ignores this
  connection's own writes; no staleness bound). All three were real; each got a
  red test, then the key became data_version + total_changes + the anchor's
  content hash, with a 30 s maximum age.
- **Cost.** While a task is running its own appends change `total_changes`, so
  the sweep re-runs each poll then (0.2-0.3 s on the 283-task record); idle,
  a poll is ~0.3 ms.
- **Reverse.** `SWEEP_MAX_AGE_S = 0` disables reuse.

## U20 - POSIX refuses multiply-linked regular files in the gate (D70)

- **Why.** POSIX has no call that lists a file's other names, so "is any name
  protected" cannot be answered; the safe answer is refusal. Directories are
  exempt (two or more links always). The cost is a workspace file hard-linked
  elsewhere is unreadable to agent tools on Linux/macOS - rare, and refusing is
  the direction that cannot leak a grader.
- **Reverse.** `platform.posix.link_names` could read `/proc`-less inode scans
  under the project root and return the names found.
