# Independent audit — iteration 3 (branch `iteration-3`)

Date: 2026-09-29. Auditor came to the branch cold, having not seen the work.
Scope: `git diff 1d911cc..HEAD` on `iteration-3` — the eleven commits
`git log --oneline 1d911cc..HEAD` lists, from `38edfa1` (test isolation) to
`611a137` (coverage + SSE tail; landed mid-audit, noted below).

Python `C:\Users\char1\.alpha-test-1-venv\Scripts\python.exe`, Windows 11,
CPython 3.11.15. **Suite green at entry**, run before touching anything:

```
python -m pytest -q -p no:cacheprovider           (parallel, the default)
  1148 passed, 26 skipped, 14 warnings in 214.97s        # first run
```

Two further runs (a new commit, `611a137`, landed during the audit and added
tests): **1159 passed, 26 skipped** in 116 s and 104 s. Three runs, zero
failures, no flakiness observed. The count rose because `611a137` added
`tests/test_sse_live_tail.py` and `tests/test_gate_mutants.py` gained two
cases; the working tree carries that commit plus a formatting-only CRLF touch
to `test_gate_mutants.py` that git reports as modified.

Evidence lives in `docs/evidence/audit-iteration-3/`. Run the pytest probes:

```
set PYTHONUTF8=1 && set PYTHONIOENCODING=utf-8
python -m pytest docs/evidence/audit-iteration-3 -q -p no:cacheprovider -n 0
```

A test named `test_FINDING_*` asserts the CURRENT (unfixed) behaviour, so it
PASSES today and a fix turns it red. `test_clean_*` records a check that came
back clean. Scripts (`probe_*.py`, `probe_*.js`) print their evidence and are
quoted inline. Every probe runs against scratch state
(`ALPHA_DATA_DIR`/`ALPHA_CONFIG_DIR` + platformdirs redirected to temp dirs by
`docs/evidence/audit-iteration-3/conftest.py`); the operator's real
`%LOCALAPPDATA%\alpha-test-1\` was byte- and mtime-identical before and after
(checked twice). Nothing under `src/`, `tests/`, `bench/`, `scripts/`,
`packaging/` or any other doc was changed. FIX NOTHING was observed.

This project documents its own sandbox as advisory against hostile code
(HANDOVER C4, DEFERRED D32) and its POSIX/macOS paths as never-run
(PLATFORMS.md). Neither is reported as a finding. What is reported is behaviour
weaker than documented, a new surface the docs do not cover, or a defect class
the branch introduced.

---

## THREAT MODEL

The system is a local, plan-first agent harness. What is worth protecting, who
could reach it, where the boundaries are, and — per HANDOVER C4 — what it does
not defend against.

### Assets
- **The database** (`agent.db`/`agent.local.db` and its `-wal`/`-shm`/`-journal`
  sidecars): every task's goal, answer, tool output, plan, approval. In
  checkout mode iteration 3 moves it to `<user data>/checkouts/<name>-<hash>`.
- **The anchor** (`anchor/`, kept outside the DB): the hash-chain checkpoints
  that make a rewrite of the DB detectable.
- **The graders** under `grading/` and the eval answer keys: whoever reads them
  can make a task score without doing the work (the adjacency class, C3).
- **Tier-1 memory** (`<user data>/memory/MEMORY.md`): prepended to every
  prompt, so a line planted here steers every future session.
- **The console bearer token** (`<user config>/.alpha_token`): authorises every
  control-plane route — rewrite the reviewer policy, add skills, start runs,
  decide approvals.
- **The reviewer policy** (`policy.md`), config files, `skills_store/`, `runs/`,
  the measurement lock, `cal.json`.
- **The user's own files** outside the workspace.

### Actors
- **A mistaken or prompt-injected model** — the documented threat model
  (HANDOVER C4: "a mistaken agent, not a hostile one"). It emits tool calls and
  plan text; the capability gate and the two-touch plan-signing are the
  controls.
- **Model-written code in the sandbox** (`run_code`): arbitrary Python under an
  advisory audit hook + a Windows Job Object. C4/D32: advisory against code that
  means harm; ctypes/native goes around it.
- **Another local process or user** on the same machine: can open the loopback
  port, can read files the OS lets it read, can create a filesystem link in a
  workspace before a task runs.
- **A web page in the user's browser**: can issue cross-origin requests to the
  loopback port; cannot read cross-origin responses (SOP), cannot forge the
  bearer token, is refused by Origin/Host checks and now cannot frame the
  console (CSP).
- **Untrusted file content**: a file in the workspace, or dropped through the
  console; its bytes are stamped UNTRUSTED and the taint ceiling keeps them out
  of side-effect arguments.
- **A stranger messaging the Telegram bot**: dropped unless allowlisted;
  hashed-not-stored (out of scope for this diff — no channel change here).

### Trust boundaries
1. Chat/goal text and observed content → UNTRUSTED; only the plan (from the
   trusted request) drives control flow (`security/capabilities.py`).
2. The capability gate: the one control that is a boundary. Argument taint ≤
   tool ceiling; path arguments within `artifact_scope`; protected state
   refused whatever the scope.
3. The control plane: bearer token + Origin + Host (loopback), now + CSP.
4. The sandbox: **advisory** path/network/spawn fences + a **kernel-enforced**
   Job Object (memory/CPU/process-count/timeout). Advisory ≠ a wall (D32).
5. The two-touch model: a human signs a plan hash before anything runs.

### What the project says it does NOT defend against (C4/D32)
- Hostile code in `run_code`: the sandbox is advisory; ctypes/native bypasses
  every Python-level fence, file size is unlimited on Windows.
- A second user or untrusted code was the named trigger to reopen the container
  decision (D32).
- The desktop `alpha.exe` is an arbitrary-Python interpreter for anyone who can
  run it (prior audit §B2, accepted).

The findings below are read against this model: a finding matters when it lets
an actor reach an asset across a boundary the docs claim holds, or when a
control the branch added is weaker than its own docstring says.

---

## SUMMARY OF FINDINGS

| id | sev | area | one line |
|---|---|---|---|
| S1 | high | control/files.py, capabilities.py | A drop creates a protected file (`.env`) through its `::$DATA` stream name; name/realpath check misses it for a not-yet-existing file |
| S2 | high | tui/app.py `_decide` | TUI "allow once" decides — and runs — another task's parked action the operator was never shown |
| S3 | medium | capabilities.py, files.py, tools.py | Hard links (`os.link`) are invisible to realpath, so every realpath-based reader/writer treats a workspace hard link to a grader/policy as in-scope |
| S4 | medium | config.py, paths.py, capabilities.py | A checkout's moved state lands under the installed-mode root; a desktop root-scoped task reads (and the gate permits writing) another checkout's whole database |
| S5 | low | control/guard.py | A 500 (route crash, or the F9 non-ASCII token crash) returns with **none** of the security headers, contradicting "every response, refusals included" |
| S6 | low | control/server.py | The `_task` 404 helper is used only by the file routes; `plan`/`approve`/`reject`/`run`/`pause` still 500 on an unknown task id |
| S7 | low | paths.py `sync_root` | The synced-folder fix misses Box, Nextcloud, MEGA, Proton Drive, Sync.com, Synology Drive — their checkouts keep syncing the database |
| B1 | medium | scripts/mutate.py | The recheck counts a **timeout** as `killed-by-suite`; the same instrument bug the commit claims to have fixed, moved one phase later |
| B2 | low | scripts/mutate.py | The whole-suite recheck runs with `-x`; any unrelated failure (a flaky test, a busy real lock) marks a survivor killed, and which test is not recorded (DEVNULL) |
| B3 | low | control/files.py | An over-long drop path is an unhandled 500, not a 4xx |
| I1 | medium | integrity.py, panels.py, server.py | The 6 s console poll runs `verify_all` twice (`/alerts` + `/panels/integrity` on open); it is O(tasks) and reaches ~10 s per poll at 1000 tasks |
| I2 | medium | execute/tools.py, control/files.py | The D61 per-file check rebuilds `task_state_paths` for every walked file: grep 6–10× slower; a 2000-entry file listing takes ~27 s |
| — | clean | CSP/headers, XSS sinks, command palette, junction fix, TUI two-touch, threading, D60, inventory, sync-state gate protection, PID identity, parallel isolation | see "Examined and clean" |

No critical findings. Two highs (S1, S2), both reachable only by an actor that
already has console/TUI access or local filesystem write into a workspace — a
narrower attacker than a web page, but inside the documented "mistaken or
compromised operating surface".

---

## SECURITY

### S1 — HIGH — a drop creates a protected file through its `::$DATA` stream name
*Class: DR3 (a control guarding one spelling of the route to the bytes) / DR4-adjacent.*

`is_protected` (`src/agent/security/capabilities.py:216`, names at
`:227-236`, `PROTECTED_FILES = ("config.yaml", "policy.md", ".env")` at `:26`)
matches a protected file by its final **name**, and otherwise by `realpath`.
`realpath` only strips a Windows alternate-data-stream suffix (`name::$DATA`)
when the file **already exists**. So for a protected file that does *not* yet
exist, `.env::$DATA` is neither caught by name (the name is `.env::$data`, not
`.env`) nor by realpath (nothing to resolve) — and opening `.env::$DATA`
writes the main stream of a real `.env`. The drop route (`control/files.py:87`
`drop` → `:42` `_allowed`) uses exactly these checks, and `within_scope`
permits it under a root-wide scope `["."]`.

Attacker: a local process or user that holds the console token (or has
drag-and-drop into a root-scoped task), planting a `.env` the app's
`load_dotenv` later reads — e.g. an `ANTHROPIC_API_KEY` that flips a run off
`--profile local`, or other env the harness trusts. `read_file` of the same
spelling is safe (you cannot read a file that does not exist); the writer is
the hole. A named-stream write onto an existing protected file
(`policy.md:hidden`) also succeeds, leaving the main content intact but
attaching hidden bytes.

EVIDENCE: `test_a_file_routes.py::test_FINDING_a_drop_creates_a_protected_file_through_its_data_stream_name`
and `::test_FINDING_a_drop_writes_a_named_stream_onto_a_protected_file` (both
pass). The first: `drop(".env")` → 403, `drop(".env::$DATA", b"ALPHA_TOKEN=known")`
→ 200, and `(root/".env").read_bytes() == b"ALPHA_TOKEN=known\n"`. Read-side
and existing-file spellings are refused
(`test_clean_root_scope_still_refuses_protected_state_by_stream_and_dot_spellings`,
6 params pass — `policy.md::$DATA`, `POLICY.MD::$data`, `policy.md.`,
`policy.md ` , `agent.db::$DATA`, `grading./…` all 403).

Impact: crosses the gate's protected-state boundary to plant a credentials/
policy file — an asset the whole gate exists to protect. Bounded by needing a
root-wide scope and console/drop access, so high rather than critical.

Suggested fix (suggested only): before the name and realpath checks, reject any
path component containing `:` beyond a drive letter (or normalise the stream
suffix away) so `name::$DATA` and `name:alt` are judged as `name`; and match
`PROTECTED_FILES` after that normalisation.

### S2 — HIGH — the TUI decides and runs another task's unseen approval
*Class: DR1 width (a control scoped narrower than what it acts on) / adjacency — the same shape as the prior audit's F1.*

`AlphaTUI._decide` (`src/agent/tui/app.py:393`):

```python
pending = self.ctx.pending_approvals(self.task_id) or self.ctx.pending_approvals()
a = pending[0]
self.ctx.resolve_approval(a["id"], approve)
...
self.attach(a["task_id"]); self._run(a["task_id"])   # action_allow/deny
```

When the task on screen has nothing parked, `a` (allow once) / `d` (refuse)
fall back to the **oldest pending approval of any task**. The approvals tab
only ever renders `pending_approvals(self.task_id)` (`redraw`, `:238`), and the
side list shows `"wants <tool>: parked"` with no preview (`refresh_side`,
`:142`) — so the operator presses `a` having seen neither the approval id nor
its `render_preview`. `_run` then calls `run_task` directly, which has no
`plan_required` guard (that lives only in the server's `/run`, `server.py:229`,
and the CLI, `cli.py:155`) — so the other task also runs with no plan drafted
or signed, defeating the two-touch model the branch spent D65 restoring.

EVIDENCE: `test_c_tui_decides_unseen.py::test_FINDING_allow_once_approves_another_tasks_unseen_action`
(passes, `plan_required=True` set to match the product):

```
approvals tab before the key press: []
approval of the OTHER task is now: approved ; TUI attached to other task: True
other task: plan events [] ; model calls 1 ; status done
the unseen write was carried out: OVERWRITTEN
```

Impact: an irreversible control (approve + run a parked write) fires on a task
the human never reviewed, with no plan signature — the console's F1 fix
(callbacks decide only their own channel's approvals) has no analogue here.
High. The web console's `decide(id, d, taskId)` (`ui.js:322`) passes an
explicit id and is not affected; this is TUI-only.

Suggested fix: drop the `or self.ctx.pending_approvals()` fallback — decide only
an approval belonging to the attached task, or none; and route the resume
through the same `plan_required` check the server uses.

### S3 — MEDIUM — hard links are invisible to the realpath-based gate
*Class: DR3 (the D61 sweep enumerated symlink/junction routes to the bytes but not the hard-link route).*

D61 made `within_scope` and `is_protected` judge `realpath` as well as the
literal path (`capabilities.py:151,158-159,221-224`), and `execute/tools.py`
grep/glob judge each walked file with `_reachable` (`:530`). A **hard link**
(`os.link`) is not a symlink or junction: it is a second directory entry for
the same file, and `realpath` returns the link's own path, not the target. So
a hard link inside a workspace pointing at a grader or `policy.md` reads as an
ordinary in-scope file to every realpath-based reader — the file browser,
`read_file`, `grep` — and to the writer.

The sandbox fences `os.link` (`sandbox.py:368` `_FS_EVENTS` includes it, and
`_allowed` refuses a src outside the workspace), so **model code cannot create
the link**. The exposure is a link created by another local process/user, or a
pre-existing one, in a workspace before or between tasks — the "another local
process" and "untrusted file content" actors. That is why this is medium, not
high: it needs an out-of-band writer, and the sandbox's own `_is_protected`
(also realpath-based, `:219`) shares the blind spot but the spawn fence covers
the in-band case.

EVIDENCE: `test_a_file_routes.py::test_FINDING_a_hard_link_in_the_workspace_reads_the_grader`
(the file route returns the grader's bytes; `gate_tool_call` on `read_file`
allows it; `tool_read_file` and `tool_grep` both return the secret) and
`::test_FINDING_a_hard_link_lets_write_file_rewrite_the_reviewer_policy` (the
gate does not refuse a `write_file` to a hard link of `policy.md` on
path grounds). Both pass.

Suggested fix: on Windows, `GetFileInformationByHandle` link count > 1 (or a
volume+file-id comparison against the protected set) catches a hard link where
realpath cannot; or refuse creating files with a link count > 1 in a workspace.
At minimum, note it in D61's writeup as the one link route realpath cannot see.

### S4 — MEDIUM — a checkout's moved state is readable from the installed-mode root
*Class: DR1 width — state relocated into a directory the other mode's gate does not key on.*

Part 1.1 moves a synced checkout's DB and anchor to
`user_data_dir()/checkouts/<name>-<hash>` (`paths.py:103` `checkout_state_dir`,
`config.py:402` `_synced_state_dir`). That same `user_data_dir()` is the
**root** of installed mode — the desktop app sets `ALPHA_DATA_DIR` to it
(`desktop.py:44-50`). The installed root's gate protects its own state by name
(`PROTECTED_DIRS`/`PROJECT_STATE`) and by the active config's configured paths
(`state_paths`, `capabilities.py:73`). `checkouts/` is neither: it is not a
protected name, and it is not any installed config's `db_path`. So a desktop
task whose scope is its root (`["."]`) can `read_file` a checkout's moved
database — every checkout task's goals and answers — and the gate does not
refuse a `write_file` to it on protected-state grounds (it parks for review,
but the human preview does not reveal the path IS a foreign database).

`state_paths` does add `cfg.state_dir` to the protected set (`:98-99`), so a
checkout protects **its own** moved dir; the gap is one config's state sitting
under another mode's root, unprotected by that mode.

EVIDENCE: `test_d_moved_state_crosses_modes.py::test_FINDING_an_installed_mode_task_reads_a_checkouts_moved_database`
(passes):

```
checkout db, relative to the desktop root: checkouts/proj-ddeea52e/agent.db
is_protected under the desktop's state: False
gate on read_file: allow
read_file returned the checkout's task goal: True
gate on write_file over it: needs_approval - write_file with user arguments
```

Impact: cross-context read of the primary asset (the task DB). Requires a
root-scoped installed-mode task on a machine that also has a synced checkout;
plausible on exactly the developer machine this feature targets. Medium.

Suggested fix: add `user_data_dir()/checkouts` to `PROJECT_STATE` (or protect
the whole `checkouts/` subtree in `state_paths`), so any config rooted at the
user data dir treats every checkout's moved state as protected.

### S5 — LOW — a 500 carries none of the security headers
*Class: the branch's own contract (guard docstring: "On EVERY response, refusals included").*

`AccessGuard.dispatch` (`guard.py:128`) adds `SECURITY_HEADERS` to the response
`_dispatch` returns. An exception thrown in a route (or in the guard itself)
never returns through that path — Starlette's outer `ServerErrorMiddleware`
writes a bare 500. So a 500 has no CSP, no `X-Frame-Options`, no `nosniff`,
no `Referrer-Policy`. This also lands on the still-open F9 non-ASCII-token
crash (`?token=café` → `secrets.compare_digest` `TypeError`, `guard.py:180-183`),
which is reachable with no auth.

EVIDENCE: `test_b_headers_and_auth.py::test_FINDING_a_500_carries_none_of_the_security_headers`
(passes): both a route crash and `GET /tasks?token=café` return 500 with all
four headers missing. The 34 `test_clean_*` cases confirm the headers ARE
present on 401, 403, 404, 405, 422 and on `/`, `/ui.js`, `/ui.css`, and on the
rebinding refusal.

Impact: low — a 500 body carries no script to protect, and in production the
traceback goes to `desktop.log`, not the body. But it is a stated-contract gap;
`nosniff` in particular is claimed on every response.

Suggested fix: wrap the app in an exception handler (or a second, outer
middleware) that re-applies `SECURITY_HEADERS` to error responses; fix F9 so a
non-ASCII token returns 401 not 500.

### S6 — LOW — unknown task ids still 500 on the plan/run routes
*Class: robustness / DR5 (the 404 helper was added but not swept across siblings).*

The new `_task` helper maps `load_task`'s `KeyError` to a 404
(`server.py:140`), but only `GET`/`POST /tasks/{id}/files` call it. The routes
the console actually hits for an unknown id still call `load_task` directly:
`POST /tasks/{id}/plan` (`:207`), `/plan/approve` (`:213`), `/plan/reject`
(`:346`), `/run` (`:222`), `/pause` (`:275`) — each a 500.

EVIDENCE: `test_b_headers_and_auth.py::test_FINDING_unknown_task_ids_are_500s_on_the_task_routes`
(passes): `{'/tasks/nope/plan': 500, ...'/tasks/nope/run': 500,
'GET /tasks/nope/files': 404}`.

Impact: low (a 500 rather than a clean 404, plus a traceback in `desktop.log`).
Suggested fix: route these through `_task` too.

### S7 — LOW — the synced-folder fix misses several sync clients
*Class: DR4-adjacent (a denylist of provider names; absent providers mean "not synced").*

`sync_root` (`paths.py:87`) detects OneDrive by env var and, by folder name,
`onedrive*` plus `_SYNC_NAMES` (dropbox, icloud, google drive/my drive, mobile
documents, cloudstorage, box sync, pcloud drive). It misses Box's current
default `Box`, Nextcloud, MEGA, Proton Drive, `Sync` (Sync.com), and Synology
Drive.

EVIDENCE (`docs/evidence/audit-iteration-3/` inline probe run against
`sync_root`):

```
C:\Users\x\Box\proj              -> None
C:\Users\x\Nextcloud\proj        -> None
C:\Users\x\MEGA\proj             -> None
C:\Users\x\Proton Drive\proj     -> None
C:\Users\x\Sync\proj             -> None
C:\Users\x\SynologyDrive\proj    -> None
G:\My Drive\proj                 -> G:\My Drive          (detected)
C:\Users\x\OneDrive - Contoso    -> ...OneDrive - Contoso (detected)
C:\Users\x\iCloudDrive\proj      -> ...iCloudDrive        (detected)
```

Impact: low on the documented Windows/OneDrive machine; a checkout under one of
the missed clients keeps uploading the database, the exact leak part 1.1 exists
to stop. Suggested fix: add the missing names; or, more robustly, detect a
`.nomedia`/provider marker, or make the refusal opt-out rather than
name-matched.

---

## BUGS

### B1 — MEDIUM — the mutation runner counts a timeout as a kill in the recheck
*Class: DR7 / instrument bug — the very one commit `e570b67` says it fixed, moved one phase later.*

`e570b67` made a timeout "undecided" in the targeted phase (`mutate.py:169`,
`:269-273`). But the `--recheck` phase (`:256-260`) reads anything that is not
`"survived"` as `killed-by-suite`:

```python
st, _ = _run(t, ["tests", ...], 900)
if st != "survived":
    r["status"] = "killed-by-suite"
```

`_run` returns `"timeout"` on a `TimeoutExpired` — so a survivor that merely
times out under contention in the whole-suite recheck is recorded as killed,
and the reported score rises with load again. This is the same failure the
commit message attributes to the first run ("most 'kills' were timeouts under
contention").

EVIDENCE: `test_g_mutate_runner.py::test_FINDING_a_timeout_in_the_recheck_is_counted_as_a_kill`
(passes): with `_run` stubbed to return `"timeout"` for the recheck, every
survivor comes back `killed-by-suite`, score `1.0`.

Suggested fix: in the recheck, treat `"timeout"` as undecided too (leave the
status `"survived"` or mark it `"timeout"`), the same rule the targeted phase
now uses.

### B2 — LOW — the recheck's `-x` lets an unrelated failure count as a kill
*Class: instrument robustness.*

The whole-suite recheck runs `pytest ... -x` (`_run`, `:161`) with output to
`DEVNULL` (`:163`). The first failure anywhere in the suite stops the run and
returns `"killed"` — so a flaky test, a port already in use, or the real
measurement lock being busy (the `real-measurement-lock` xdist group also runs
under `-n 12`) marks the survivor killed, and `DEVNULL` means the run does not
record which test "killed" it.

EVIDENCE: `test_g_mutate_runner.py::test_FINDING_any_failure_anywhere_in_the_whole_suite_is_a_kill`
(passes): the fake whole-suite run returns `"killed"`, every survivor becomes
`killed-by-suite`, and the recheck argv is `["tests","-n","12","--dist","loadgroup"]`
with no `-x` visible to the caller (it is added inside `_run`).

Suggested fix: drop `-x` in the recheck and require the specific mutated test(s)
to fail, or capture output so a spurious kill is auditable.

### B3 — LOW — an over-long drop path is an unhandled 500
*Class: robustness / fail-open of error handling.*

`drop` (`control/files.py:87`) validates base64 and size but not path length or
component length. A drop whose path has a >255-char component, or is ~40 KB
long, raises `OSError` out of `mkdir`/`open` and is a 500 (with no security
headers, per S5) rather than a 4xx.

EVIDENCE: `test_a_file_routes.py::test_FINDING_an_overlong_drop_path_is_an_unhandled_500`
(2 params pass): `component-over-255` → 500, `path-40k` → 500. No partial
directories were left behind on this volume (`\\?\` extended paths). Reserved
device names are handled fine — see `test_clean_reserved_device_names_do_not_reach_a_device`
(NUL abspaths to `\\.\NUL`, outside every scope → 403; CON/COM1/`nul.txt`
create ordinary files on this Windows 11 build).

Suggested fix: catch `OSError` in `drop`/`browse` and return 400/404.

---

## INEFFICIENCY

### I1 — MEDIUM — the 6 s poll runs an O(tasks) `verify_all`, twice per open panel
The console polls every 6 s: `ui.js:896` calls `loadAlerts()` (→ `GET /alerts`)
and `loadSide()`. `/alerts` (`server.py:293`) calls
`verify_all(...)`, which walks every task (`integrity.py:239` `for task_id in
sorted(known)` — `log.verify` reads and re-hashes each task's whole chain) plus
now the anchor-name sweep (D60, `:225-234`). Opening the Integrity or Control
Center pane calls `/panels/integrity` (`panels.py:61`), a second `verify_all`.

Measured (`probe_f_poll_cost.py`, synthetic DBs, 60 events/task, this machine):

```
tasks events verify_all  /alerts  /conversations /approvals poll total share of 6 s
  100   6000     0.55s     0.95s     0.086s        0.145s     1.18s      20%
  300  18000     1.87s     2.39s     0.026s        0.018s     2.43s      40%
 1000  60000    11.59s     9.67s     0.039s        0.068s     9.77s     163%
```

`/conversations` and `/approvals` are cheap and well-indexed; `verify_all` is
the whole cost, and at ~1000 tasks one `/alerts` alone (9.7 s) exceeds the 6 s
poll interval — polls pile up, and a pane open adds a second full sweep. The
283-task real record is ~2 s per poll today and grows linearly.

Suggested fix: `/alerts` need not re-verify the entire history every 6 s —
verify incrementally (only tasks whose head moved since the last sweep), cache
the result with a short TTL, or verify only non-terminal tasks on the poll and
full-sweep on demand.

### I2 — MEDIUM — the D61 per-file check rebuilds the protected set for every file
`_reachable` (`execute/tools.py:530`) calls `task_state_paths(ctx, task)` and
two `realpath`s for **every** file grep/glob walks; `task_state_paths` re-globs
`config*.yaml`, stats each, and rebuilds the whole tuple each call (the
`_sibling_state` `lru_cache` helps only its inner part). The console's file
browser is worse: `browse` calls `_visible` → `_allowed` →
`task_state_paths` + realpath for every directory entry (`control/files.py:52,66`).

Measured (`probe_g_walk_cost.py`, this machine):

```
grep over 2000 files: 1.52s with _reachable, 0.15s without (10.1x)
task_state_paths alone: ~1.9 ms per call
GET /tasks/{id}/files on a 2000-entry directory: 26.96s (os.listdir alone 3.7 ms)
```

A 2000-entry workspace directory takes ~27 s to list in the console — the
`_allowed` call per entry dominates. Correctness is fine (the check is right);
the cost is rebuilding an invariant-per-task value once per file.

Suggested fix: compute `task_state_paths(ctx, task)` (and the realpath'd
protected roots) once per grep/glob/browse call and pass it in; the per-file
work is then two `realpath`s, or a single `st_dev/st_ino` membership test.

### Note (not ranked) — coverage commit landed mid-audit
`611a137` ("coverage reported 83%") landed during the audit and is inside the
audited range now; its `test_sse_live_tail.py` and the two `test_gate_mutants.py`
cases are green. No finding; noted so the commit range is honest.

---

## EXAMINED AND CLEAN

- **CSP and security headers on the normal paths.** `SECURITY_HEADERS`
  (`guard.py:111`) is a strict `default-src 'none'` with `script-src`/`style-src
  'self'`, `frame-ancestors 'none'`, `base-uri 'none'`, `form-action 'none'`,
  no `unsafe-inline`/`unsafe-eval`, plus DENY/nosniff/no-referrer. Present on
  200, 401, 403, 404, 405, 422, on `/`, `/ui.js`, `/ui.css`, and on the DNS-
  rebinding refusal — 34 cases in `test_b_headers_and_auth.py`. Only the 500
  path misses them (S5).
- **Auth and Origin on every new route.** All 16 new routes 401 without the
  token and 403 with a foreign Origin even with the token
  (`test_b_headers_and_auth.py`, 32 cases). Matches
  `tests/test_console_api_v2.py::test_every_new_route_needs_the_token`.
- **XSS sinks in ui.js.** Every `innerHTML` template rendered with a hostile
  string in every field (model output, plan/goal/scope, file names, tool
  previews, memory entries, channel sender, skill/suite names, integrity
  detail) produces no live markup, and every `data-args` attribute round-trips
  to exactly its input values — the attribute-context escaping the prior
  audit's H1 flagged is closed by the `esc(JSON.stringify(...))` +
  `JSON.parse(decode(...))` design. `probe_h_ui_render.js` (Node, real ui.js in
  a stub DOM): 15 templates, 16 data-args round-tripped, 0 broken, 0 raw
  `<img`. `esc` (`ui.js:50`) escapes `& < > " '`.
- **The command palette and click dispatch.** `ACTIONS` (`ui.js:852`) is an
  explicit object; the click handler (`:857`) and palette (`COMMANDS`, `:788`)
  only dispatch keys present in it — `el.dataset.action` is guarded by
  `ACTIONS[...]`. Inherited `Object.prototype` keys (`constructor`, `toString`,
  `valueOf`) exist on the object but none is a registered action and none is
  reachable as a `data-action` string that does anything (they are functions
  from the prototype, not entries the handler routes to a UI effect); the
  palette runs only from its own fixed `COMMANDS` list. No page content reaches
  an action outside the map.
- **The junction fix (D61).** Traversal spellings (`..`, mixed slash, case,
  `.`-segments, trailing-dot dirs), absolute and `\\?\` extended paths, 8.3
  short names, and a junction created **before** the task by another writer are
  all refused by the file routes; a junction is neither listed nor followed.
  `test_a_file_routes.py` (13 traversal params + absolute + 8.3 + pre-existing
  junction, all pass); matches `tests/test_junction_escape.py`. The hard-link
  route is the one link realpath cannot see (S3).
- **The sandbox junction/spawn/network fences.** `_winapi.CreateJunction` is in
  `_FS_EVENTS` (`sandbox.py:368`), `os.link`/`os.symlink` are fenced with both
  ends judged, `_NET` refuses socket creation incl. loopback, `_SPAWN` refuses
  process starts. Advisory by design (D32); `_winapi.CreateProcess` (prior
  audit H2 / D49) remains a documented, accepted hypothesis and is unchanged
  here.
- **TUI two-touch on the normal path.** Submitting drafts a plan and runs
  nothing until `s`; `b` records a reason and runs nothing
  (`tests/test_tui_two_touch.py`, and re-read of `app.py:321-366`). The break is
  only the cross-task approval fallback (S2).
- **TUI threading.** The live bus fires on the worker thread and every widget
  touch hops back via `call_from_thread` (`app.py:206-218`); drafting and
  running are on daemon threads that only `call_from_thread` back
  (`:336-352,413-423`). No widget is touched off the UI thread.
- **D60 integrity sweep.** `verify_all` now sweeps `anchor.all_tasks() - known`
  and flags an anchored id with no DB row as critical, excluding `plan:` pins
  (`integrity.py:225-234`). No false positive from another config: each config
  has its own `anchor_path`, and the moved-state migration creates a fresh
  empty anchor beside the fresh DB. Matches
  `tests/test_chain_mutants.py`.
- **Inventory change (D62).** The three rows now count what the code writes
  (`session.opened` tombstone payload; skill `status`), not event types nothing
  appends. `_count`'s new `tombstoned`/`skill_status` kinds
  (`inventory.py:167-176`) read the real signals; the DBs are opened read-only
  (`take`, `:190`). No skill is promoted anywhere but `skills/gate.py:312`, so
  counting `status IN ('promoted','demoted')` cannot over-count.
- **Sync-state gate protection.** `state_paths` adds `cfg.state_dir`
  (`capabilities.py:98`), so a synced checkout's moved DB/anchor is protected
  for that checkout (`tests/test_sync_folder_state.py`). The gap is only
  cross-mode (S4).
- **PID identity checks.** `_terminate` opens with `QUERY_LIMITED` and compares
  `GetProcessTimes` creation time through the same handle it terminates on
  (`windows.py:21-33`); POSIX uses a pidfd where available and killpg only
  after confirming identity (`posix.py:68-116`). A real sleeper survives a kill
  pinned to the wrong start time (`test_h_pid_identity.py` confirms the pin is
  honoured); an unreadable start time turns the pin off (S-adjacent note below).
  Matches `tests/test_recycled_pids.py`.
- **Parallel test isolation.** Three full suite runs, no flakiness. The
  `real-measurement-lock` and `wheel-build` xdist groups keep the tests that
  take the real checkout lock / build in the repo `build/` on one worker
  (`test_calibration_path.py:27`, `test_noise_main.py`, archived bench suite,
  `test_the_wheel_ships_no_tasks.py`). Fixed ports use `bind(("127.0.0.1", 0))`
  (`test_m12_serve.py`, `test_cli_subcommands.py`); `11435` references are
  config values, not live binds. Real user dirs untouched across all runs.

Two smaller, honestly-partial notes:
- **Unreadable start time turns the kill pin off** (`proc.py:93`,
  `windows.kill_tree_signal`): when `process_start_time` returns `None`,
  `_terminate(pid, None)` skips the identity check and terminates whatever holds
  the pid — the fail-open direction, where `descendants()` (`proc.py:74`)
  deliberately fails closed. `test_h_pid_identity.py::test_FINDING_an_unreadable_start_time_turns_the_pin_off`
  passes. Low, and arguably intended (a kill that cannot verify should still
  try), but it is the opposite convention to the listing side, so worth a note.
- **Child pins are re-read after the listing, not carried from it**
  (`windows.py`/`posix.py` `kill_tree_signal`): the commit says "the start time
  seen at listing travels with each pid", but `descendants()` returns bare
  pids and `kill_tree_signal` calls `process_start_time` again, so a pid
  recycled in the microseconds between is pinned to the new process.
  `test_h_pid_identity.py::test_FINDING_a_childs_pin_is_read_after_the_listing_not_at_it`
  passes. The window is tiny and the parent kill precedes children (leaves
  first), so exposure is minimal; noted for accuracy against the commit claim.

---

## NOT EXAMINED (and why)

- **Live model, GPU, or network paths.** No API key, no Ollama; the harness is
  offline by design. `--profile local` and the Anthropic/Telegram adapters
  (D26) were out of scope and unchanged in this diff.
- **The desktop freeze / PyInstaller build.** `packaging/` is unchanged in the
  diff and a build is ~5 min + not a security surface this iteration touched;
  the prior audit covered the frozen exe (accepted §B2).
- **POSIX/macOS kill and pidfd paths.** `posix.py` changed but only Windows was
  exercised (the platform this machine is); PLATFORMS.md already marks POSIX
  as WSL2-once and macOS never-run. The Windows half was probed directly.
- **The two leftover control-plane processes on 8766/8767.** Running since
  2026-09-28 22:03 (before this session), owned by an earlier run; not started
  by this audit and not touched (the task reserves those ports for scratch
  planes). All processes this audit started were in-process pytest or
  self-cleaning subprocesses; a spawned sleeper in the PID probe was killed
  (confirmed exit).
- **The full mutation run** (`scripts/mutate.py` over taint/capabilities): the
  runner's *logic* was probed (B1/B2) with `_run` stubbed; an actual multi-hour
  mutation sweep was not re-run — it is the builder's tool, and its correctness,
  not its latest score, was the audit target.
- **Deep review of unchanged modules** (loop.py step machinery, the taint
  tracker internals, the skill gate arithmetic): outside the diff; the prior
  audit and INVARIANTS cover them.

---

*Operational note.* Everything ran against the live copy. No network egress
beyond loopback. No git, branch, or history changes. The only repository
additions are this file and `docs/evidence/audit-iteration-3/`. The operator's
real `%LOCALAPPDATA%\alpha-test-1\` (database, memory, token, anchor) was byte-
and mtime-identical before and after the audit, verified twice; the real
memory line `- [user] injected instruction` (D67, the prior audit's leftover)
is still present and remains the user's to remove — this audit did not touch
it.
