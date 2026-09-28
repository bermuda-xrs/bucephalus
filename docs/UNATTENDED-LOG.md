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
