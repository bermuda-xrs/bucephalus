# The unattended run - what happened while you were away

Iteration 3, 2026-09-28 to 2026-09-30, on branch `iteration-3` (from `main` at
`1d911cc`). Every judgment call is in `docs/DECISIONS-UNATTENDED.md` (U1-U22),
the running record in `docs/UNATTENDED-LOG.md`. This page is the five-minute
version.

**Main was fast-forwarded to `iteration-3`** again at the end of the
continuation, because the three conditions held at the final commit: the full
suite green, the frozen gate passing on a build of it, and both audits'
critical and high findings fixed (there were none of either, the second time).
Nothing was pushed; there is no remote.

## After you came back, 2026-10-04

Errors and dead code, as asked. **Deleted 20 definitions nothing called** (U21),
porting first the two rules only their tests checked: the quarantined
summarizer is given no tools, and routing moves off a slow tier (that test
could not fail before). **Two resource leaks in the product**, found by
running the suite with warnings on: every code action left the child's
stderr pipe open, and a refused job left its child's pipes open. Source that
a later Python will reject (invalid escapes) and a BOM that hid a script from
every AST audit are fixed, with a test that keeps them out. **ruff now lints
for bugs in CI** (U22; new dev dependency). Three sentences the code no
longer made true are corrected, including README's `alpha.cmd` examples,
which fail at this path. Windows 1288 passed, WSL2 1262, frozen gate 63.

## The continuation, 2026-09-30

You were still away, so I worked the open-defect list. Closed, each with a
test written first: **D70** (a hard link in the workspace to a grader or
`policy.md` read as an ordinary file to every check - a real hole in the gate),
**D49** (agent code could start a process through `_winapi.CreateProcess`,
which raises no audit event; the Job Object now holds only the interpreter's
own processes, so the kernel refuses a spawn by any route), D46-D48, D54-D59
(the Linux defects, fixed and checked in WSL2), D63, D72, D74-D77, D80-D85, and
the enabling half of D27 (receipts now claim before an effect, so the chain
lock no longer freezes every append during a slow push, and a crash inside a
send is reported as unknown, never repeated).

**Checked from outside, twice.** A commit-time security review found the D72
cache's key too weak (three real gaps; fixed). A second independent audit of
the day's commits (`docs/AUDIT-ITERATION-3B.md`, committed unedited): 0
critical, 0 high, 2 medium, 3 low - all regressions of the day's own changes,
all fixed. Linux (WSL2, Python 3.13): whole suite 1257 passed, none failed; it
also found two tests that needed the network. The frozen app was rebuilt from
the final code: gate 63 passed, a task through its window, nothing left
running. Mutation testing of the day's new code (receipts, the hard-link check, the
console cache, the guard, the Windows platform functions) found the claim
sentinel forgeable - a tool returning `{"claimed": true}` would have read back
as an unknown outcome - and the console poll unprotected against a skipped
memory check; both fixed, scores 64-94% before and 83-100% after
(`docs/evidence/mutation-2026-09-30/`).

**Measured, not changed:** the in-loop token estimator undercounts by ~26%
(D6; server = 1.108 x estimate + 193, 180 calls), so the context ceiling fires
~15% late. Fixing it changes when compaction fires, which is prompt bytes:
proposal **P6**, with the measurement.

## What needs you

In the order I would take them:

1. **Your real memory holds a line a test wrote** (U2). The audit's probe put
   `- [user] injected instruction` into
   `%LOCALAPPDATA%\alpha-test-1\alpha-test-1\memory\MEMORY.md` on 2026-09-25,
   and it has been in the prefix of every real session since. The suite no
   longer opens real memory (38edfa1); removing the line was yours to do:
   `.\alpha.ps1 memory --forget "injected instruction"`.
2. **A graded program can print its own verdict** (D79, proposal P5). Found
   while mutation-testing the verifiers: `verify_program` reads the first
   verdict marker on stdout, and the grader runs the answer inside its own
   process. A strict xfail shows it. Nothing recorded shows it happening.
   Every fix changes what counts as a pass (HARNESS_VERSION), so it waits.
3. **Five more proposals**, each with its measurement (`docs/PROPOSALS.md`):
   P1 a real network fence on Windows (AppContainer: persistent ACL changes),
   P2 what the model sees each step and what it costs, P3 whether a
   follow-up task should see the conversation before it, P4 a tray icon,
   **P6 calibrating the token estimator** (the ceiling fires late; a
   HARNESS_VERSION matter).
4. **D26**: a bot token and an API key; the two live tests are written and
   skip cleanly without them. The Telegram bot's queue and acknowledgement
   (what is left of D27) wait with it: that transport has never made a live
   call, and I would not change it untested.
5. **A Mac and a bare-metal Linux box**: `docs/PLATFORMS.md` section 5 is the
   checklist. CI is written (`.github/workflows/ci.yml`) and has never run:
   there is no remote, and pushing was not mine to do.
6. Optional: the measurement record gained two empty tables from a test
   (D80, U11); one `DROP TABLE` each if you want its schema as measured.

## Run the new console

```powershell
cd "C:\Users\char1\OneDrive\Desktop\Βουκεφάλας\alpha-test-1"
.\alpha.ps1 --config config.local.yaml serve
# then open the link it prints: 127.0.0.1:8901, with the token in the fragment
```

It needs the model server from HANDOVER B2 step 3 (`.\ollama-serve.ps1`, port
11435) to do real work. Not `alpha.cmd`: `.cmd` files fail at this path (B1).
The same console in a window: `C:\Users\char1\.alpha-build\dist\alpha\alpha.exe`
(rebuilt with `python packaging\build.py` from the build venv). The terminal
UI: `.\alpha.ps1 --config config.local.yaml tui`.

## What changed

**Part 1 - security.** A checkout in a synced folder (this one is in OneDrive)
keeps its new database, anchor and run records outside it (U1; nothing was
moved for you). Every kill checks the process's identity through the handle
or pidfd that sends it, so a recycled pid is never hit. The console refuses
framing and runs under a strict CSP with no inline script (D45). The network
fence is still advisory; the only no-admin kernel fence is P1. Memory is not
compared against the chain, by measurement (U3).

**Part 2 - tests.** The suite runs in parallel by default: 228 s serial,
about 55 s now on an idle machine (180 s on a loaded one - U10). Mutation
testing of the security core with an in-repo runner (U5); the share of
decided mutants killed, every survivor re-run against the whole suite:

| module | first honest run | final |
|---|---|---|
| the capability gate | 83.5% | 90.8% |
| taint propagation | 76.2% | 76.2% (the five left are dead code) |
| scope / retrieval isolation | 73.3% | 86.7% |
| the hash chain | 60.0% | 89.0% |
| anchor and integrity sweep | 79.5% | 93.6% |
| the prompt contract's allowlist | 92.3% | 100% |
| grader hold-out: the mesh / the reviewer transcript | 56.2% / 16.7% | 81.2% / 50.0% |
| the verifiers | 58.4% | 91.1% |

Every survivor left has its reason in
`docs/evidence/mutation-2026-09-28/README.md` (equivalent, dead code, or a
limit not worth a test). Four timing tests fail under load and were the only
"killer" of some mutants; those count as survivors (U13). Coverage is 83%,
reported not chased; what is under 70% is subprocess-run code. CI is
prepared, UNVERIFIED.

**Part 3 - efficiency**, before (`main`) and after, interleaved, medians
(`docs/evidence/iteration-3-efficiency/`):

| | before | after |
|---|---|---|
| the console's integrity sweep, 283-task record (every poll) | 0.92 s | **0.29 s** |
| desktop app: close after a task | - | 22.3 s, then **3.4 s** (D81) |
| desktop app: close, idle | 2.99 s | 2.63 s |
| desktop app: spawn to first paint | 4.59 s | 4.55 s |
| desktop app, idle, whole tree: working set / private | 501 / 280 MB | 512 / 283 MB |
| per agent step, harness only | 33.3 ms | 31.9 ms |
| `alpha serve` to /health | 2.63 s | 2.58 s |
| desktop build | 63 MB | 62 MB |

**The close target (under 2 s) is not met** (U12): the app's own work ends
0.3-0.4 s after the window is closed; the rest is Windows tearing down the
WebView2 host, and no faster exit exists. No process is left behind. The
database already had WAL, the indexes and FTS5. What the model is shown each
step is proposal P2, not a change.

**Part 4 - the console and the TUI.** Three panels: conversations with a
Needs-you queue; the thread with its composer; the task's workspace. A task
is plan-first: the plan is a card you sign, with what to settle first, the
irreversible steps marked, and the plan's hash in a seal. Approvals park inline
and in Needs you. The context ring shows the window and the compaction
ceiling. A Control Center holds Memory, Schedules, Channels, Skills, Integrity,
Measurement, Settings and Logs; Ctrl/Cmd+K is a palette over the same actions.
Dark by default, light available, WCAG AA for every text pair (lowest
5.39:1), keyboard-complete, focus kept in dialogs. A tampered chain shows in
the one reserved colour. Dropped files land in the workspace only, marked
untrusted. The TUI is plan-first too (it used to run on Enter). No tray icon
(U7, P4). **The frozen app was rebuilt, its gate passes, and a scripted task
ran through its own window** - typed, drafted, signed, answered - with nothing
left running after the close.

**Part 5 - an independent audit** (`docs/AUDIT-ITERATION-3.md`, committed
unedited). No critical. Two high, both fixed red-first: a protected file could
be created through its NTFS stream name (`.env::$DATA`), and the TUI's allow
key could decide and run another task's unseen approval. Two mediums were
regressions of this run and were fixed (U8); the rest are in DEFERRED.

## Screenshots

`docs/evidence/ui/`: the console in eight states, both themes, desktop and
phone, and a tampered chain; the frozen app's own window; the TUI. The ones to
look at: `plan-card-dark-desktop.png`, `parked-approval-light-desktop.png`,
`tampered-long-running-dark-desktop.png`, `plan-card-dark-phone.png`,
`frozen-app-task-done.png`, `tui-signed-and-done.png`.

## Decisions, most important first

- **U9 - D79 left unfixed**: fixing how a program's verdict is read changes
  what counts as a pass, which is HARNESS_VERSION; it is P5.
- **U1 - what leaves a synced checkout**: only runtime state (database,
  anchor, run records); tracked graders and skills stay; nothing existing was
  moved. The 283-task record still sits in OneDrive until you move it.
- **U2 - your memory's test line left in place**: real memory files were off
  limits; the command to remove it is above.
- **U8 - two audit mediums fixed** rather than deferred, because this run had
  introduced them.
- **U11 - the record keeps the two empty tables** a test added (D80): dropping
  them would be another write to the record.
- **U12 - the close target reported as unmet**, and the build not trimmed.
- **U3 - memory not compared against the chain**: the naive wiring alarmed on
  ordinary use; the chain is audit evidence, stated as such.
- **U6 - the console's design**, **U4 - no slow marker** (the whole suite is
  the default, in parallel), **U5 - an in-repo mutation runner** (mutmut does
  not support Windows), **U13 - how flaky kills were counted**, **U10 - suite
  timing is machine state**, **U7 - no tray**.

## Found along the way

Defects in the product, each fixed red-first unless marked:
- **D61 (high)**: a directory junction led the gate, the sandbox and grep out
  of the workspace to the graders.
- **D68, D69 (high)**: the audit's two, above.
- **D81**: after a task, the desktop app took 22 s to close, and a stop with
  the console open never finished - the new console's live stream held the
  server; every server now bounds that wait.
- **D82**: the TUI's input row and status bar were off screen while every TUI
  test passed. Found by the screenshots.
- **D64**: the console never offered a real plan for signature (it waited for
  "draft"; the planner writes "drafted"; the tests seeded "draft" by hand).
- **D65**: the TUI ran a task on submit with no plan.
- **D60**: a task deleted from the database was invisible to `alpha verify`.
- **D80**: a test migrated the measurement record's schema on every run.
- **D79 (open)**: above.

And in my own instruments, each caught before it reached a number here:
- The mutation runner's first pass said 100% everywhere (timeouts counted as
  kills); the audit found two more of the same kind (D78).
- The first "phone" screenshots were cropped desktops.
- The first frozen-app runs showed "orphans" in old and new builds alike:
  exited processes whose start time stays readable while a handle is open.
- The first `serve` startup figures were quantised by the probe's own 2 s
  connect timeout.

## Not done, or not verified

- **Close under 2 s**: not met (above).
- **Linux is checked in WSL2, not bare metal**, and its new code (`adopted`,
  zombie handling) could not be mutation-tested there.
- **CI and POSIX**: the workflow has never run; macOS has never run anything;
  Linux ran three times, in WSL2 only.
- **One full-suite failure was not captured**: a run of the final code failed
  one test that `--lf` then passed, and every full run since was green. The
  load-sensitive timing tests above are the likely source; the other flakes
  seen today were diagnosed and fixed (the two reaper tests).
- Screen-reader use was checked through the accessibility tree only.
