# Unattended run - progress log (iteration 3)

Branch `iteration-3`, from `main` at `1d911cc`. Main is fast-forwarded only at
the end, and only if the suite, the frozen gate and the Part 5 audit pass.

## Part 1 - medium security findings

- **Found first: the suite wrote the operator's real memory** (38edfa1). See
  DECISIONS U2 - waiting for the user.
- **1.1 state out of synced folders** - database, anchor and run records of a
  checkout under OneDrive/Dropbox/iCloud/Google Drive go to platformdirs;
  notice once; nothing moved (DECISIONS U1). `test_sync_folder_state.py`,
  red first. The serve test's server had no scratch user dirs and opened the
  real memory directory - fixed in the same change.
- **1.2 recycled PIDs** - already closed in Phase E (D42: start-time rule,
  `kill_tree(expect_start=)`); re-swept every signalling site. The gap left:
  a pid validated when the tree was listed was signalled later by NUMBER.
  Now the identity check goes through the kill's own handle (Windows:
  OpenProcess TERMINATE|QUERY, GetProcessTimes, TerminateProcess on the
  same handle) or pidfd (Linux >= 5.3); macOS has no pidfd, so there it is
  check-then-signal, stated in the code. Popen-owned kills act on our own
  unreaped child and cannot hit a recycled pid. Red first:
  `test_a_kill_pinned_to_another_start_time_signals_nothing` (real kernel),
  `test_posix_checks_identity_before_it_signals` (fake kernel).
- **1.3 planner** - already closed in Phase E (D43); re-read: the survey
  reads only scope entries, through the gate's own `is_protected`,
  including "contains protected state". No change.
- **1.4 memory vs chain** - NOT wired, by measurement (DECISIONS U3).
- **1.5 anti-framing / CSP** - D45 closed. Every response carries the
  headers; the console's script and styles are files; buttons dispatch from
  an explicit action map. Browser check: no CSP violation, actions work.
  Four tests that read ui.html for content that moved now read the new file
  (same assertions); the wheel's asset list gained ui.js and ui.css - that
  list exists to force exactly this review.
- **1.6 network fence** - researched, not built: the only non-advisory
  no-admin mechanism is AppContainer, which needs a registry profile and ACL
  grants on the shared Python install - persistent security changes to the
  user's machine. Written up as `docs/PROPOSALS.md` P1; HANDOVER C4 now
  says the audit-hook fence is advisory and names the option.

**Part 1 done** (commits 38edfa1..HEAD). Full suite at 0287833: 1063 passed,
25 skipped. Next: Part 2, tests.

## Part 2 - tests (in progress)

- **2.1 baseline**: serial full suite 227.6 s (1063 passed, 25 skipped; this
  machine has ranged 228-381 s serially). Slowest: CPU-limit kill 8.0 s,
  wheel build 6.9 s, TUI 6.1 s, CLI subprocesses 2-3 s each - each is the
  check itself.
- **2.2 parallel (pytest-xdist 3.8.0, new dev dependency)**: default is now
  `-n auto --dist loadgroup`. Shared-state bugs found and fixed: the wheel
  build ran in the repo's build/ from several workers at once; three files
  take the REAL checkout's measurement lock and read "lock busy" when run
  concurrently. Each is pinned with an xdist_group. After: 46.7 s and
  47.5 s, two consecutive clean runs (1063 passed). No test deleted, no
  timeout shortened. `-n 0` runs serially.


## Part 4 - UI (in progress)

- **4.4 file browser and drag-and-drop, backend** - `control/files.py`:
  GET/POST `/tasks/{id}/files`, through `within_scope` + `is_protected` on
  the path as given and as resolved; listings hide protected names; a drop
  never overwrites and is recorded as `human.uploaded`, taint untrusted.
  `tests/test_file_browser.py` was red against a naive route (25 of 26).
- **Found on the way: D61 (HIGH), closed** - a junction led the gate, the
  sandbox and grep/glob out of the workspace to the graders. Fixed at the
  shared checks; `tests/test_junction_escape.py` red first.
- **4.1-4.3 the console** - three panels (conversations and Needs you; the
  thread with its composer; the workspace file browser), a Control Center
  (Memory, Schedules, Channels, Skills, Eval suites, Reviewer policy,
  Integrity, Measurement, Settings, Logs), Ctrl/Cmd+K palette over the
  existing action map only, dark by default and light available, vanilla
  JS under the Part 1 CSP. The plan card is the centre: objective, what to
  settle first, numbered steps with the irreversible ones marked, the hash
  in a seal, Sign and run / Send back. The context ring shows the window
  AND the compaction ceiling. Streaming output with the prefill state, tool
  cards with their receipts (tool.receipt streamed as tool.returned: name,
  ok, ms - never content). Deep links in the fragment (conv, task, pane,
  theme). Clicked through in a browser: message -> drafted plan -> signed
  -> ran -> answer; a parked approval; a tampered chain in the reserved
  colour.
- **API** - conversations (a thread spanning tasks; D30 option 3's grouping
  half - feeding earlier exchanges to the model changes prompt bytes, so
  that half is PROPOSALS P3), /context, /memory (+ accept), /panels/*.
  `tests/test_console_api_v2.py`.
- **Found: the console never offered a real plan for signature.** It waited
  for plan status "draft"; the planner writes "drafted". The console tests
  seeded "draft" by hand, so they were green. Fixtures now draft through
  `planner.store_plan`, and a static test holds every status the page
  compares against to the set the planner writes (red first).
- **Found: "It carries on" was false.** Deciding an approval only recorded
  the decision; nothing resumed the parked run. Both surfaces now resume it.
- **4.5 the TUI** - plan-first (it used to run a task on submit, no plan, no
  signature: `tests/test_tui_two_touch.py`, red first), conversations and
  Needs you down the side, the live plan panel, a tool-trail tree with
  delegated children nested, streaming output, one status bar with model,
  context meter against the ceiling, budget and integrity count; Ctrl+K is
  Textual's palette. New Textual hooks recorded in KNOWN_INERT with reasons.
- **Flake fixed** - `test_reap_kills_exactly_what_selection_returns` read the
  live process table twice; under a parallel suite it changed in between.
  It now reads one frozen view (the property is the plumbing, not the table).
- **4.7 verified visually** - 40 screenshots in `docs/evidence/ui/` (8 states
  x dark/light x desktop/phone, plus a tampered chain), README there with the
  method, WCAG AA contrast for every pair (lowest 5.39:1) and the keyboard
  checks. Phone shots go through DevTools device emulation: headless Edge
  will not lay out below ~500 px from a window size (the first phone set was
  a cropped desktop and was replaced). Focus now stays inside an open dialog
  and returns on close.
- **4.6 tray** - not built: a second library and a process that outlives its
  window, against the no-orphans rule (DECISIONS U7, PROPOSALS P4).
- **3.7** - PROPOSALS P2 with measurements (schemas 1,215 tok/step, prefix
  819 vs 668 tok with and without code-as-action); P3 for conversation
  history. No prompt bytes changed.
- **2.3 mutation - INSTRUMENT BUG FOUND IN MY OWN RUNNER, re-run under way.**
  The first full queue reported 100% everywhere; 134 of 190 verifier "kills"
  were TIMEOUTS under 10-way contention, and the whole-suite recheck had never
  been shown to pass unmutated. A timeout is now undecided (neither kill nor
  survivor) and the recheck must pass unmutated first. The corrected queue
  runs from a snapshot worktree (`~/.alpha-mut-snap`, removed at the end).

## Part 2.4 - coverage, reported not chased

`pytest --cov=agent` (pytest-cov 7.1, new dev dependency): **83% of 7,933
statements**. Coverage follows the in-process suite only; code the suite runs
in SUBPROCESSES - the CLI (46 subprocess tests), the ASGI entry, the frozen
dispatcher, `__main__` - reads as uncovered although it runs. Under 70%:

| module | cover | what the uncovered lines are |
|---|---|---|
| `__main__.py`, `control/asgi.py` | 0% | entry points; run as subprocesses by the CLI and serve tests |
| `frozen.py` | 26% | the frozen child dispatch; run by the frozen gate, not the suite |
| `cli.py` | 30% | every subcommand; run as subprocesses (test_cli_subcommands) |
| `instrument/report.py` | 44% | run reports and CSVs for `alpha report` / bench; subprocess-run |
| `models/ollama.py` | 50% | the live streaming loop against a real Ollama; tests use fakes |
| `platform/posix.py` | 54% | the Linux/macOS half; runs on POSIX only (WSL2 run in Phase D) |
| `skills/retrieve.py` | 61% | skill retrieval ranking branches |
| `control/events.py` | 62% | **the SSE live tail - the console streams from it** |
| `control/server.py` | 68% | routes exercised by the serve test's server subprocess |

Added where it matters: `tests/test_sse_live_tail.py` (replay then live, in
order, ephemeral ids past the chain; red with the tail cut off).


## Part 5 - independent audit

`docs/AUDIT-ITERATION-3.md`, by a fresh subagent, committed unedited
(f6e293f). No critical. Two HIGH, both fixed red-first (fc17455): S1 a
protected file created through its NTFS stream name (and the same blind spot
in the sandbox's fence, found by the sweep); S2 the TUI deciding and running
another task's unseen approval. Of the mediums, S4 and I2 were regressions of
this run and were fixed (DECISIONS U8); S3 hard links and I1 poll cost are in
DEFERRED with the lows (D70, D72, D74-D77). B1/B2, the audit's findings on my
mutation runner, were fixed because they corrupt numbers this report states.

## Part 2.3 - mutation, the authoritative pass (2026-09-29)

The fixed runner's recheck (queue4, survivors of v2 against the whole suite)
left real gaps in the hold-out and the graders. Killed red-first (02554fc,
f67897f): a missing or raising verifier could pass; the reviewer transcript's
held-out branch; retrieval with no task ids; a rehashed gap in the chain; the
anchor's hash filter and directory; and the verifiers' list, boolean, schema,
citation, pytest, ruff and execution checks. **Found killing them: D79**, a
graded program can print its own verdict (strict xfail; P5, U9). Final scores
come from queue5 (a fresh run at f67897f with the new tests targeted).

## Found while waiting (2026-09-29)

- **D80**: a 2026-09-06 test opened the 283-task measurement record
  read-write, so every schema addition since reached it; this run's two
  conversation tables did. No rows. Read-only now, red first (U11).
- **D72, partly**: the /alerts sweep read the anchor file twice per task, one
  read unused - two thirds of the sweep. Once per sweep now, red first.
- **3.2**: pywebview's private-mode close waits up to 3 s for WebView2 to exit
  on its own; the tree is now stopped on the window's `closed` event, and the
  private profile removed if pywebview skipped it. Red-first test; NOT yet
  checked frozen - uncommitted until 4.8 shows it.
- The suite took 180 s where 2.2 measured 47 s: the same old commit took 155 s
  in the same hour, so it is the machine (U10).
- The reaper test's taskkill timed out under load; os.kill is TerminateProcess.

## Part 2.3 - final (5c9c9f6)

queue5 (fresh run at f67897f, new tests targeted, whole-suite recheck).
Kills made only by load-sensitive timing tests counted as survivors (U13).
Final: gate 90.8% (with three junction tests that pinned the real-path halves
of the scope and protected checks, each hidden behind the other), taint 76.2%,
scope 86.7%, chain 89.0%, anchor 93.6%, prompt allowlist 100%, mesh 81.2%,
transcript 50.0%, verifiers 91.1%. docs/evidence/mutation-2026-09-28/.

## Part 3 - efficiency (5c9c9f6..1f9143e)

Interleaved before/after, idle machine (docs/evidence/iteration-3-efficiency/):
sweep 0.92 -> 0.29 s; per step 33.3 -> 31.9 ms; serve to /health 2.63 ->
2.58 s (the first figure, 3.08 s both, was the probe's own connect timeout);
serve idle 62 MB. Startup is half FastAPI's import; left. DB: WAL, indexes,
FTS5 already; every new console query indexed. Build 63 -> 62 MB, not trimmed
(U12).

## 4.8 - the frozen app (6dfdab5, 1f9143e)

Rebuilt; the frozen gate passes (55 passed, 1 skipped by design). A scripted
task ran through the window's own controls over DevTools. Found: **D81**, a
close after a task took 22.3 s (uvicorn waited for the console's stream with
no limit) - 3.4 s now, every server bounded; the early stop's handler
returned a list into pywebview's set (desktop.log); idle close 2.99 -> 2.63 s.
The 2 s target is not met (U12): our work ends 0.3-0.4 s after WM_CLOSE, the
rest is the OS tearing down the WebView2 host. No orphans, by exit code - my
first harness counted exited-but-referenced processes as orphans. The TUI
screenshots found **D82**: its input and status bar were off screen.

## Part 6 - report

docs/UNATTENDED-REPORT.md; HANDOVER section D, B3, E2, E3; DEFERRED's
summary; DECISIONS U12-U13.

## RESUME (2026-09-29, final)

All parts done or reported as not done (the report's last section). Left:
cleanup (worktrees ~/.alpha-mut-snap and ~/.alpha-before, dist-before-alpha,
temp alpha-* dirs), a final rebuild + gate + suite, the fast-forward.
