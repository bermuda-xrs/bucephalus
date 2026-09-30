# Independent audit — iteration 3B (branch `iteration-3`)

Date: 2026-09-30. A second auditor, cold to the work, taking the branch after
the first audit (`docs/AUDIT-ITERATION-3.md`) was written and its findings
swept. Scope: `git diff e13eef5..HEAD` on `iteration-3` — the nine commits
`git log --oneline e13eef5..HEAD` lists, from `41af423` ("the open defects,
swept") to `b38ab3c` (DECISIONS U14-U20). These commits close the first
audit's S1/S3/S5/S6/S7/B1-B3 and the deferred D46-D77 backlog, add
claim-then-execute receipts (D27) and a cached integrity sweep (D72), and land
the POSIX fixes (D54-D58) checked once in WSL2.

Python `C:\Users\char1\.alpha-test-1-venv\Scripts\python.exe`, Windows 11,
CPython 3.11.15; POSIX re-run in WSL2 Debian, CPython 3.13.x. **Suite green at
entry**, run before touching anything:

```
python -m pytest -q -p no:cacheprovider           (parallel, the default)
  1267 passed, 1 failed, 27 skipped, 1 xfailed in 60.05s
```

The one failure is an artefact of the audit harness, not the code:
`tests/test_paths.py::test_a_checkout_keeps_state_beside_the_source` asserts
`state_root(...) == ROOT`, and it fails only because this audit exported
`ALPHA_DATA_DIR` to a scratch path for isolation, which `state_root` honours by
design. Cleared with `ALPHA_DATA_DIR` unset, `test_paths.py` is 7/7 green
(`-o addopts=`). WSL2 Debian at HEAD: **1244 passed, 50 skipped, 1 xfailed**
(one deselect, the same `ALPHA_DATA_DIR`-scratch test). No other failures, no
flakiness across the entry run and the WSL run.

Evidence lives in `docs/evidence/audit-iteration-3b/`. Run the pytest probes:

```
set PYTHONUTF8=1 && set PYTHONIOENCODING=utf-8
python -m pytest docs/evidence/audit-iteration-3b -q -p no:cacheprovider -o addopts=
  44 passed
```

A test named `test_FINDING_*` asserts the CURRENT (unfixed) behaviour, so it
PASSES today and a fix turns it red. `test_clean_*` records a check that came
back clean; `test_note_*` records behaviour that is defensible but reads
narrower or wider than a docstring/commit claim. Scripts (`probe_*.py`) print
their evidence and are quoted inline. Every probe runs against scratch state:
`docs/evidence/audit-iteration-3b/conftest.py` reuses `tests/conftest.py`
(which redirects the config dir and `platformdirs.user_data_dir` to temp dirs)
and additionally sets `ALPHA_DATA_DIR` to a temp dir, in process and in
subprocesses. The operator's real `%LOCALAPPDATA%\alpha-test-1\` and
`%APPDATA%` token were never read or written; port 11434 was never touched. No
`src/`, `tests/`, `docs/` file was modified except this report and
`docs/evidence/audit-iteration-3b/`. FIX NOTHING was observed.

This project documents its sandbox as advisory against hostile code (HANDOVER
C4, DEFERRED D32) and its POSIX/macOS paths as WSL2-once / never-run
(PLATFORMS.md). Neither is reported as a finding. What is reported is behaviour
weaker than a control's own docstring/commit claim, a defect the branch's new
code introduces, or a claim in a commit message that is not actually true.

---

## THREAT MODEL (unchanged from the first audit; summarised)

**Assets.** The event database and its WAL/anchor sidecars; the out-of-DB
anchor (the hash-chain checkpoints); the held-out graders and answer keys;
tier-1 memory (prepended to every prompt); the console bearer token; the
reviewer `policy.md`, config, `skills_store/`, `runs/`.

**Actors.** A mistaken or prompt-injected model (emits tool calls and plan
text; the capability gate and two-touch plan-signing are the controls);
model-written code in the sandbox (advisory Python fences + a kernel-enforced
Job Object / rlimits; ctypes and native code go around the advisory half);
another local process or user (loopback port, files the OS permits, a
filesystem link planted in a workspace); a web page (Origin/Host/CSP + bearer
token); untrusted file content (stamped UNTRUSTED, kept out of side-effect
arguments by the taint ceiling).

**Boundaries.** (1) chat/observed content → UNTRUSTED, only the plan drives
control flow; (2) the capability gate — the one boundary; (3) the control
plane — token + Origin + Host + CSP; (4) the sandbox — advisory fences + a
kernel-enforced Job Object; (5) two-touch: a human signs a plan hash before a
run.

**What the project says it does NOT defend against (C4/D32).** Hostile code in
`run_code` (advisory sandbox; ctypes/native bypasses it; file size unlimited on
Windows); a second user / untrusted code (the named trigger to reopen the
container decision); the desktop `alpha.exe` as an arbitrary-Python
interpreter.

A finding matters when it lets an actor reach an asset across a boundary the
docs claim holds, or when a control the branch added or changed is weaker than
its own docstring / commit message says. Read against that model, **this branch
introduces no security regression that defeats a control with default config.**
The findings are two robustness/DoS regressions the new code introduces and
three lower-impact accuracy gaps between a claim and the behaviour.

---

## SUMMARY OF FINDINGS

| id | sev | area | one line |
|---|---|---|---|
| M1 | medium | capabilities.py `_protected_elsewhere` (D70) | `os.stat` on an over-long path raises `ValueError`, not `OSError`: it CRASHES any run whose model emits a >32,767-char path, 500s the file-browse route, and leaves the 40 KB drop path a 500 — so D77's "unwritable drop path → 400" is only half true |
| M2 | medium | kernel/receipts.py `execute_once` (D27) | the fill-in step after a successful effect is not exception-safe: if its commit raises (another writer holding the lock — the documented multi-writer case), the effect has happened but the receipt stays `_CLAIM` and the process-global `_inflight` Event is never set, so a same-process retry (a resume re-issues the same key) blocks on `done.wait()` with NO timeout — a permanent hang; the effect is also reported to the model as `ok:False` |
| L1 | low | execute/sandbox.py + platform/windows.py (D49) | the `tool.receipt` "limits" note records "32 processes" for an agent-code run whose Job Object was actually capped at 2 (or 1 frozen) — the record overstates the applied process limit ~16x, in a record the project treats as authoritative (A2-4) |
| L2 | low | plan/planner.py `parse_plan` (D46) | the degradation handler catches `(ValueError, TypeError, AttributeError)` but not `OverflowError`: plan JSON with a number like `1e400` still 500s `POST /tasks/{id}/plan` — one more shape of the exact defect D46 closed |
| L3 | low | control/server.py (D75) | "every task route 404s an unknown id" is overstated: `/metrics/{task_id}`, `/reviews/{task_id}`, and `DELETE /suites/{suite_id}/tasks/{task_id}` return 200 for an unknown id; only the `/tasks/{id}/*` routes were swept |
| — | clean | D70 gate/grep/sandbox on hard links, D27 duplicate/lost-effect/deadlock core, D72 cache (in-process, out-of-band-via-connection, anchor edit with mtime restored, edit-between-polls, memory, own-connection writes, multi-instance), D47 token bytes, D74 headers on 500, D54/D55/D56/D57 POSIX, the reaper cannot kill a daemon, flakiness-test rewrites | see "Examined and clean" |

No critical findings, no high findings. Both mediums are robustness/DoS
regressions the new code introduces; neither crosses a security boundary (each
fails closed — a crash or a hang, never access granted). The three lows are
accuracy gaps between a commit's claim and the behaviour.

---

## MEDIUM

### M1 — MEDIUM — D70's `os.stat` raises `ValueError` on an over-long path, crashing a run and 500-ing two routes
*Class: a control that turns a graceful decision into an uncaught exception (DR7 / robustness), introduced by D70; and a commit claim (D77) that is only half true.*

D70 added `_protected_elsewhere` (`src/agent/security/capabilities.py:248`),
which `is_protected` calls for every path, and it does an unconditional
`os.stat(p)` (`:259`) inside a `try/except OSError`. On Windows a path longer
than `MAX_PATH`-extended (32,767 chars) makes `os.stat` raise **`ValueError:
stat: path too long for Windows`**, which is **not** an `OSError` — so the
`except OSError` does not catch it and it propagates out of `is_protected`,
`gate_tool_call`, and `within_scope`'s callers.

The pre-D70 `is_protected` never stat-ed the raw path (it only ran
`os.path.realpath`, which returns an over-long non-existent path unchanged and
does not raise here), so this is a **regression the range introduces**, shown
by running the gate on both trees:

```
# docs/evidence/audit-iteration-3b/probe_e_long_path_gate.py
source .../src            (HEAD):   gate read_file: RAISED ValueError: stat: path too long for Windows
                                    gate write_file: RAISED ValueError: stat: path too long for Windows
source .../old/src  (e13eef5):      gate read_file: allow
                                    gate write_file: needs_approval - write_file with user arguments
```

Three surfaces are reached by it:

1. **A run crashes.** A model that emits a >32,767-char path in a `read_file`
   or `write_file` action does not get a clean gate deny; the exception
   escapes into `run_task`'s generic handler and the task is recorded
   `task.crashed` (`{"error":"ValueError: stat: path too long for Windows"}`),
   with no `human.denied`. For a measurement instrument a crash and a deny are
   different outcomes, and a prompt-injected model can force the crash on
   demand.
2. **The console file browser 500s.** `files.browse` → `_allowed` →
   `is_protected` raises; `get_files` catches only `Refused`/`Missing`, so the
   route is a 500 (it now carries the security headers, thanks to D74 — see
   clean — but it is still a 500, not a 4xx).
3. **The 40 KB drop path is still a 500.** D77's commit says "an unwritable
   drop path is a 400", and it is, for a >255-char *component* (that raises
   `OSError` from `open`, which `drop` now maps to `Unwritable`→400). But a
   ~40 KB total path raises `ValueError` from `is_protected` *inside
   `_allowed`, before* the drop's `try/except OSError` — and `ValueError` is
   not `OSError` — so it stays a 500, the same status the first audit's B3
   flagged. D77 fixed one of B3's two params and left the other.

EVIDENCE:
`docs/evidence/audit-iteration-3b/test_d_long_path_gate.py` (3 pass) —
`is_protected` raises, the gate raises, and a scripted model emitting the long
path yields `status=crashed` with `ValueError` on the chain and no
`human.denied`. `test_c_control_plane.py::test_FINDING_a_40k_drop_path_is_still_a_500`
and `::test_FINDING_browse_raises_valueerror_on_a_40k_path` (pass): the drop is
500 with the exception `ValueError('stat: path too long for Windows')`, and
`files.browse` raises the same. `test_c_control_plane.py::test_drop_of_an_overlong_path`
prints the spread: `309 chars -> 400`, `40010 chars -> 500 logged=['ValueError']`.

Impact: medium — a self-DoS (the offending task crashes; the browse/plan routes
500) trivially triggered by model output, within the documented "mistaken or
prompt-injected model" actor, but it grants no access (the file is never
touched) and does not cross a boundary. Suggested fix (suggested only): in
`_protected_elsewhere`, catch `(OSError, ValueError)` on the `os.stat` (return
`False`, as it already does for `OSError`); and, defensively, have `drop`/`get_files`
map a path-length `ValueError` to 400/404 the way they now map `OSError`.

### M2 — MEDIUM — D27's fill-in step is not exception-safe: a stranded claim hangs a same-process retry forever
*Class: an error path that leaves a lock/waiter that can never be released (deadlock), introduced by D27.*

`execute_once` (`src/agent/kernel/receipts.py:44`) is claim-then-execute: it
inserts a `_CLAIM` receipt row and creates a process-global
`_inflight[key] = threading.Event`, runs the effect, then in a final block
fills the row in and sets the Event:

```python
with chain_lock(conn):
    conn.execute("UPDATE receipts SET result_json=? WHERE idempotency_key=?", (canonical(result), key))
    conn.commit()
    _inflight.pop(key, None)
done.set()
```

The effect itself is wrapped (`except Exception` frees the claim and sets the
Event; `except BaseException` leaves the claim but still sets the Event). **The
fill-in block above is wrapped by nothing.** If `conn.execute` or `conn.commit`
raises — the realistic trigger is another writer holding SQLite's write lock
past the 30 s `busy_timeout`, which the DB module's own comment
(`kernel/db.py:14-33`) names as a live possibility ("a CLI run against the same
database while the server is up") — then:

* the effect **has already happened** (the message was sent, the push done);
* the receipt row stays `_CLAIM` (the outcome is now "unknown" on disk);
* `_inflight[key]` keeps an Event that is **never set** and is **never
  popped** (the `pop` is inside the `with` block, after the failing commit).

The exception propagates out of `execute_once`. In `loop._perform` it is caught
as a generic tool failure and the model is told **`ok:False`** for an effect
that succeeded. Worse, the next `execute_once` for the same key **in the same
process** — and a resumed task re-issues its first action under the same key,
because `_restore_park_seed` does not restore the step counter and a resumed
run restarts at step 0 (`loop.py:810`) — takes the `mine=False` branch, reads
`done = _inflight.get(key)` (the stale, unset Event), and blocks on
**`done.wait()` with no timeout** (`receipts.py:80`): a permanent hang of the
run/bridge thread. A fresh process would instead see `_inflight.get(key) is
None` and return `outcome_unknown`, so the hang is specific to a live
long-running process (the console server), which is exactly where resume runs.

EVIDENCE:
`docs/evidence/audit-iteration-3b/test_a_receipts.py::test_FINDING_a_failed_fill_in_leaves_a_stale_inflight_entry_and_the_same_key_hangs`
(pass). The probe holds a second connection's `BEGIN IMMEDIATE` across the
fill-in (busy_timeout lowered to 0.5 s only so it is quick; the production path
is identical after 30 s):

```
row={"claimed":true} inflight=True  second call still blocked after 5 s: True
```

The marker file shows the effect ran exactly once, the receipt is still
`_CLAIM`, the `_inflight` Event is unset, and a second same-key call did not
return within 5 s. `::test_FINDING_the_loop_reports_a_performed_effect_as_failed_when_the_fill_in_fails`
(pass) shows `loop._perform` writing the file, then recording the receipt as
`"ok":false` because the fill-in commit raised.

Impact: medium — a permanent thread hang on the control plane under the
documented multi-writer condition, plus a succeeded effect reported failed.
Bounded by needing a fill-in commit failure (a second writer past 30 s), which
the single-writer-process rule is meant to avoid but the code does not enforce.
Suggested fix (suggested only): wrap the fill-in in `try/finally` that always
`_inflight.pop(key, None)` and `done.set()` (leaving the row `_CLAIM` on a
commit failure is the correct "unknown" state, but the in-process waiter must
be released); and give `done.wait()` a timeout that falls through to
`outcome_unknown` rather than blocking forever.

*Note (not a finding).* When an effect **raises** (the `except Exception`
path), a thread already waiting on that key re-runs the effect itself rather
than surfacing the owner's error to its own caller
(`test_a_receipts.py::test_note_a_waiter_reruns_an_effect_whose_owner_raised`,
pass: two effect calls). This matches the pre-D27 lock-across-effect behaviour
(a retry after a failure), so it is not a regression, but the docstring's "two
threads with the same key run it once" reads narrower than "run once *if it
succeeds*". The duplicate-provable-once and lost-effect cases the commit
targets are all clean — see below.

---

## LOW

### L1 — LOW — the run's receipt records a process limit it did not apply
*Class: telemetry that does not match the enforced limit; the record is a stated deliverable (A2-4, "what limits held this run").*

D49 gives agent-code Job Objects a process cap of exactly the interpreter's own
count — `spawn_denied_processes()` returns 2 for a venv, 1 frozen — applied in
`run_sandboxed` (`sandbox.py:634-637`) via `limits["max_processes"]`. But the
`notes`/`limits` string on the chained `tool.receipt` is built from the
constant `MAX_PROCESSES` (`sandbox.py:595`, `platform/windows.py:200`), so
every agent-code run records "32 processes" while its kernel-enforced cap was 2.

EVIDENCE:
`docs/evidence/audit-iteration-3b/test_f_limits_record.py` (pass):

```
applied max_processes=2   recorded notes=['job object: memory 512 MB per process, CPU 30 s, 32 processes; ...']
```

Impact: low — the enforcement is correct (the first audit and
`tests/test_sandbox_limits.py` confirm a spawn is refused); only the recorded
number is wrong, and by a factor that would mislead anyone reading the receipt
to understand what constrained a run. Suggested fix: build the note from the
`limits` actually passed to `spawn_limited`, not from the constant.

### L2 — LOW — `parse_plan` degrades on wrong-shaped JSON but not on an overflowing number
*Class: DR5 — the D46 handler swept most shapes and missed one exception family.*

`parse_plan` (`plan/planner.py:129`) wraps `_plan_from` in
`except (ValueError, TypeError, AttributeError)` and degrades to an empty plan
with an `unknowns` note. `OverflowError` is an `ArithmeticError`, not in that
tuple: a step with `"est_tokens": 1e400` (which `json.loads` parses to
`float('inf')`) reaches `int(float('inf'))`, raises `OverflowError`, and
escapes — a 500 on `POST /tasks/{id}/plan`, the exact defect D46 closed, by one
more shape.

EVIDENCE:
`docs/evidence/audit-iteration-3b/test_g_parse_plan.py`:
`test_clean_wrong_shapes_degrade` (7 params pass — steps-an-int, step-a-string,
tokens-words, bad-taint, a-list, tokens-nan, tools-an-int all degrade
cleanly); `test_FINDING_an_overflowing_number_still_raises` and
`test_FINDING_the_plan_route_500s_on_it` (pass): `POST /plan -> 500`.
`test_note_deep_nesting` records that a 5,000-deep nested array raises
`RecursionError` (also outside the handler; a stdlib limit, noted not ranked).

Impact: low — a 500 with the model text in the server log rather than a
degraded plan; reachable only via the planner backend returning that JSON.
Suggested fix: add `ArithmeticError` (or broaden to `Exception`) to the
degradation handler, and clamp numeric fields.

### L3 — LOW — "every task route 404s an unknown id" misses three `{task_id}` routes
*Class: DR5 — the `_task` helper was swept across `/tasks/{id}/*` but not the `{task_id}` routes elsewhere.*

D75's commit says "every task route 404s an unknown id", and the
`/tasks/{task_id}` family now does (the first audit's S6 is closed — all of
`plan`, `run`, `pause`, `steer`, `approve`, `reject`, `stream`, `files` route
through `_task`). But three routes take a `{task_id}` outside `/tasks/` and do
not: `GET /metrics/{task_id}`, `GET /reviews/{task_id}`, and
`DELETE /suites/{suite_id}/tasks/{task_id}` each return **200** for an unknown
id.

EVIDENCE:
`docs/evidence/audit-iteration-3b/test_c_control_plane.py::test_note_the_task_id_routes_outside_tasks`
(pass): `{'GET /metrics/nope': 200, 'GET /reviews/nope': 200, 'DELETE /suites/nope/tasks/nope': 200}`.
`::test_every_task_route_with_an_unknown_id` (pass) confirms all eleven
`/tasks/{id}/*` routes 404.

Impact: low — an empty report / an empty decision list / a no-op delete
reported as success, rather than a 404; no data exposed. Suggested fix: route
these three through `_task` (or a suite/id existence check) too, or scope the
commit claim to the `/tasks/` family.

---

## EXAMINED AND CLEAN

- **D70 hard links, the gate / grep / sandbox (the core of the commit).** With
  a workspace file hard-linked (`os.link`) to the project's `policy.md`
  (protected by name and by state), the capability gate **denies** both
  `read_file` and `write_file` on the link and **allows** a plain sibling;
  `tool_grep` does not return the policy through the link; and sandboxed code
  cannot read it. `docs/evidence/audit-iteration-3b/test_e_hardlinks.py` (4
  pass). `link_names` (Windows `FindFirstFileNameW`) enumerates the names and
  `_protected_elsewhere` refuses a multiply-linked regular file whose any name
  is protected; POSIX `link_names` returns `None` and the file is refused
  outright (a regular file with `st_nlink >= 2`), while directories — which on
  POSIX always have `>= 2` links — are excluded by the `S_ISREG` guard, the
  fix in commit `c5881a0`. The one residual is the copy-back path, below.
- **D70 sandbox copy-back is the writer, not a gate bypass.** In copy mode (no
  symlink privilege — the default Windows install on this machine),
  `link_into` correctly skips a multiply-linked workspace file, so agent code
  finds no such file inside the sandbox. If the code then *creates* a file at
  that workspace-relative path, `_copy_back` writes it back onto the workspace
  path — which is the hard link — and the write reaches the linked target. This
  is `test_e_hardlinks.py::test_FINDING_code_rewrites_the_policy_through_the_copy_back`
  (pass), and it is **not reported as a finding**: it is the sandbox's ordinary
  copy-back doing exactly what D32 says an advisory sandbox does not defend
  against (the copy-back runs in the harness parent with no gate check, by
  design, for every copied path), and it requires an out-of-band actor to have
  planted the hard link — the same actor and the same "advisory, not a wall"
  boundary the docs already disclaim. Worth one line in D70's write-up so the
  copy-back path is not mistaken for covered; not a control weaker than its
  docstring.
- **D27 the duplicate / lost / deadlock core.** Two threads with one key run
  the effect once and both get the result
  (`test_a_receipts.py::test_clean_two_threads_one_key_run_the_effect_once`); a
  cut-off effect (`KeyboardInterrupt`, the `BaseException` path) leaves the
  `_CLAIM` and a waiter gets `outcome_unknown`, not a repeat
  (`::test_clean_a_cut_off_effect_leaves_the_claim_...`); a failed effect frees
  its claim and can be retried
  (`tests/test_m1_kernel.py::test_a_failed_effect_can_be_retried`); a slow
  effect does not hold the chain lock
  (`::test_a_slow_effect_does_not_hold_the_chain`); and a process killed inside
  the effect reports `outcome_unknown` on replay instead of repeating
  (`::test_a_run_that_died_mid_effect_does_not_repeat_it`). The one gap is the
  fill-in exception path, M2.
- **D27 the `outcome_unknown` consumers.** `codeact.make_tool_handler` records
  the receipt with `ok=False` and returns the dict to the sandbox (no repeat);
  `loop._perform` renders it as an observation. The one consumer that
  mis-labels it is `_release_decided`, which writes `approval.consumed`
  `outcome="executed"` for a resumed approved action whose `_perform` returned
  `outcome_unknown` — the action was **not** executed by this run, yet the
  chain says "executed"
  (`test_a_receipts.py::test_FINDING_an_approved_action_with_an_unknown_outcome_is_recorded_as_executed`,
  pass). This is real but narrow (it needs an approved action whose earlier run
  died mid-effect leaving a `_CLAIM` under the *same step id*), and it
  mislabels a record rather than repeating or losing an effect; folded here as
  a sub-note of M2's family rather than ranked separately.
- **D72 the cached integrity sweep — the hard cases.** Every tamper the commit
  claims to catch is caught on the next poll after a warm cache:
  an edit through another connection (`data_version` moves), an edit through
  the server's **own** connection (`total_changes` moves — the gap the
  hardening commit closed), an anchor edit **with size and mtime restored**
  (the content hash moves), an edit that lands **mid-sweep** (the key is read
  before the walk, so the next poll re-walks), and memory tampering (checked
  every poll, never cached). An in-process rewrite via `Connection.backup()`
  moves neither `data_version` nor `total_changes` and is unseen until the 30 s
  max age expires — but that is **within** the bound the docstring states and
  needs code running inside the server process, which no documented actor has.
  `docs/evidence/audit-iteration-3b/test_b_integrity_cache.py` (7 pass, incl.
  the backup case and the raw-file note below). The poll cost is now flat: on
  a 1,000-task synthetic DB a cache hit is ~0.25 ms and a poll after one event
  append (a running task's step) re-walks in ~0.8 s — the first audit's I1
  (double `verify_all`, ~10 s at 1,000 tasks) is resolved, and `/alerts` and
  `/panels/integrity` now share the one cache
  (`probe_d_poll_during_run.py`).
- **D72 raw byte-edit of the DB file (a pre-existing SQLite limitation, not a
  D72 regression).** An out-of-band writer that edits the database *file's
  bytes* directly (not through SQLite) moves neither `data_version` nor
  `total_changes`, and is invisible to the running server **even on a full
  `verify_all` re-walk through the server's long-lived connection**, because
  SQLite serves the already-cached pages; a fresh connection sees the chain
  break. This is a property of `verify_all` using the shared `ctx.conn`, which
  is **unchanged from before the range** (pre-D72 `/alerts` called the same
  `verify_all(ctx.conn, ...)` every poll), so it is not introduced by D72 and
  not ranked; noted because the task asked specifically about out-of-band
  edits, and `alpha verify` from the CLI (a fresh process/connection) is the
  path that catches it. `test_b_integrity_cache.py::test_raw_file_edit_seen_by_the_long_lived_connection`
  and `docs/evidence/.../` scratch script confirm the fresh-connection catch.
- **D47 the token compared as bytes.** A non-ASCII query token (`café`), a raw
  surrogate, a 10 KB token, `"Bearer"`, and empty/space tokens are all **401**,
  never a 500, on the query param, the `x-alpha-token` header, and the
  `Authorization: Bearer` header, sent as both UTF-8 and latin-1 bytes
  (`test_c_control_plane.py::test_clean_odd_tokens_are_401_not_500`, 5 params).
  The first audit's F9 (`?token=café` → 500) is closed.
- **D74 headers on a route exception.** A route that raises returns 500 with
  **all** the security headers and no traceback in the body
  (`test_c_control_plane.py::test_clean_a_route_exception_carries_every_header`);
  the M1 40 KB-path 500s also carry the headers. The first audit's S5 is
  closed.
- **D54/D55/D56/D57 POSIX (WSL2 Debian, CPython 3.13).** D54: an instant
  zombie is `alive()==False` and absent from `process_table()`. D55: a
  re-parented orphan **of ours** (same session, parent in another) is selected
  by `orphan_pids`; a **daemon in its own session** whose parent is killed is
  **not** selected — the reaper cannot kill it (`adopted()` returns False for
  it). D56: `connect()` passes `cached_statements=0`; six threads hammering the
  same SQL concurrently raise nothing (the "bad parameter or other API misuse"
  the commit describes does not reproduce). D57: the CPU rlimit is
  `timeout+1` (`limits["cpu"]==8` for a 7 s timeout,
  `tests/test_posix_platform.py`). Scratch scripts under the evidence dir plus
  `tests/test_reaper_parentage.py`, `test_posix_platform.py`,
  `test_recycled_pids.py` (34 pass, 3 skip in WSL). The commit's claim "18 of
  20 runs failed with the statement cache, 0 of 20 without" for D56 was not
  re-run 20x, but the mechanism (concurrent shared prepared statement) is
  confirmed gone.
- **D49 the spawn cap is enforced.** Agent code's Job Object is capped at the
  interpreter's own process count (2 here), so a spawn by any route — including
  `_winapi.CreateProcess`, which raises no audit event — is refused by the
  kernel; a harness run keeps the normal cap and can spawn
  (`tests/test_sandbox_limits.py`, both cases pass). Only the *recorded* number
  is wrong (L1).
- **The flakiness-test rewrites weaken no check.** `test_parallel_tools.py`
  now proves concurrency with a `threading.Barrier(5)` (each call blocks until
  all five are in flight; `BrokenBarrierError` otherwise) instead of comparing
  wall clocks — a *stronger* proof of parallelism, not a weaker one.
  `test_web_tools.py` and `net/politeness.py` moved to `time.monotonic()` for
  spacing (a wall-clock set back an hour no longer makes a request wait an
  hour) and the test now asserts the exact sleep with a fake monotonic clock
  (`slept == [5.0]`), tighter than the old `slept[0] >= 5`. `politeness.py`
  still uses `time.time()` nowhere for spacing (grepped); the new
  `test_D59_the_spacing_does_not_follow_the_wall_clock` pins it. No assertion
  was loosened.
- **D70 performance of `is_protected` on a large walk.** The extra `os.stat`
  per path is paid only by files with `st_nlink >= 2` (the early return on
  `< 2` skips it for ordinary files), and the realpath cache is per-2 s bucket.
  Measured over 2,000 files, grep and `is_protected` are within noise of the
  pre-range tree (grep 0.13-0.14 s both; `is_protected` ~545 µs/path both),
  i.e. **no measurable regression** (`probe_g_is_protected_cost.py`, HEAD vs
  e13eef5). The first audit's I2 concern does not worsen here.

---

## HYPOTHESES (suspected, not confirmed as findings)

- **Cross-process concurrent same-key returns a spurious `outcome_unknown`.**
  If two *processes* run the same key against the same DB (against the
  documented single-writer rule), the second reads the `_CLAIM` row, finds no
  local `_inflight` entry, and returns `outcome_unknown` while the first is
  *actively running* the effect — a false "unknown", not a duplicate (the safe
  direction). What would confirm it as a finding: evidence that the project
  intends two writer processes to run concurrently. The DB module's comment
  says the opposite (one writer process at a time), so this is left as a
  hypothesis, not ranked.
- **The M2 hang could also strand the codeact bridge thread.** `execute_once`
  is called from `codeact.make_tool_handler` on the sandbox bridge thread; the
  same fill-in-failure + same-process-retry would hang that thread too. Not
  separately reproduced (the resume/`_perform` path in M2 is the more direct
  trigger); would be confirmed by a sandboxed side-effecting tool retried
  under a held write lock.

---

## NOT EXAMINED (and why)

- **Live model, GPU, or network paths.** No API key, no Ollama contacted (port
  11434 untouched by rule); the harness is offline by design. `--profile
  local` and the Anthropic/Telegram adapters were out of scope and unchanged
  in this diff. The D27 commit notes the Telegram queue/ack wait "with D26 —
  untested code on the one path that talks to strangers"; that channel code was
  not exercised here (no channel change in the diff) and the receipt behaviour
  was audited through the in-process callers instead.
- **The desktop / uvicorn graceful-shutdown and window-closed early-stop
  (item 7).** `desktop.py`/`cli.py`/`server.py` shutdown paths are unchanged in
  `e13eef5..HEAD` (the diff touches `server.py` only for `_task` and the
  `Unwritable` handler); the bounded-shutdown and window-closed work landed
  before `e13eef5` and was not re-driven — a build is ~5 min and it is not a
  surface this range changed.
- **A real multi-hour mutation sweep** (`scripts/mutate.py`) — the first
  audit's B1/B2 are in that file, which is unchanged in this range; not re-run.
- **The two hard cases of M2 under a real second-process lock.** M2 is
  reproduced with an in-process second connection holding `BEGIN IMMEDIATE`
  (identical to the 30 s-timeout production path but faster); an actual second
  OS process holding the lock past 30 s was not staged (it would only slow the
  same proof).
- **macOS (`platform/darwin.py`).** Never run anywhere (PLATFORMS.md); the
  darwin module re-exports the POSIX `adopted`/`link_names`/`spawn_denied_processes`
  added here, so it inherits their behaviour, but this was read, not executed.
- **Deep review of modules outside the diff** (loop step machinery, taint
  tracker internals, skill-gate arithmetic) — covered by the prior audit and
  INVARIANTS; only their interaction with the changed code (receipts, gate,
  panels) was checked.

---

*Operational note.* Everything ran against scratch state; the operator's real
user directories and the Ollama on 11434 were never touched. No network egress
beyond loopback (the test client is in-process). No git, branch, or history
change. Every process this audit started — in-process pytest, self-cleaning
subprocesses, and short-lived WSL fork/daemon probes — was confirmed gone at
the end (`ps` in WSL clean; no python.exe left from this audit; no
control-plane listener bound). The only repository additions are this file and
`docs/evidence/audit-iteration-3b/`. FIX NOTHING was observed.
