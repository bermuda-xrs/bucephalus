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

## RESUME (written 2026-09-28, usage limit reached)

Branch `iteration-3`, clean after the commit that adds this section. Main
untouched. Next exact step: Part 2.2's slow marker (optional - the parallel
run is already 47 s), then 2.3 mutation testing (mutmut or equivalent on
security/capabilities, security/taint, kernel/scope, kernel/events +
integrity, assure/verifiers, the prompt-contract allowlist, the grader
hold-out), 2.4 coverage report, 2.5 CI file (UNVERIFIED). Then Parts 3-6.
Parts 3.1 (build 188 -> 62 MB) and 3.2 (close 8.66 -> 3.60 s) were already
done in Phase F; 3.2's <2 s target is open.

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
