# Deferred defects

**A defect freeze is in force from 2026-09-04.** Eleven defects were found in
one session across the instrument and process classes, every one of them real.
But the project has never completed a full measurement, and the instrument is
now good enough to produce one. So from here: **new findings get logged here,
not fixed.** Something is fixed only if it would invalidate a measurement that
is currently running.

Each entry carries evidence and a severity, so a later session can triage
rather than re-derive.

**Severity**
* **blocking** — would invalidate a result. Fix before the next measurement.
* **corrupting** — silently changes a number without failing. Fix before
  publishing anything that depends on it.
* **cosmetic/generality** — real, but the current iteration's conclusions do
  not rest on it.

Two exceptions were granted at freeze time and are already done: the
pathological-call detector (blocking) and the trap decomposer (Phase 2B's
instrument). Everything below waits.

---

## D1 — Working set is not a clean per-process leak signal

**Severity:** corrupting, for any future leak diagnosis.

`proc.worker_rss_gb()` reads `WorkingSetSize`, which Windows **trims** under
pressure. One L6 refresh recorded RSS going *up* across a successful reload,
2.28 → 3.251 GB, because the worker had been trimmed before it was reloaded.
The same effect showed as `ollama app.exe` reporting 3728 MB working set
against **60 MB private** in samples seconds apart.

**Correct version:** report `PagefileUsage`/`PrivateUsage` (private commit)
alongside working set, and key any future trigger off private commit.

**Why deferred:** the L1 refresh is driven by the commit ratio and verified by
the *bounded, non-monotonic* RSS series, which holds despite the trimming
noise. No current conclusion rests on the absolute RSS value.

---

## D2 — The cause of the pathological model call is unknown

**Severity:** blocking for the cost axis, mitigated but not diagnosed.

Three occurrences: 2465 s (v3 ladder probe), 2539 s for 512 tokens in L6
(~0.2 tok/s against a normal 2–3), and 1499.8 s historically. That is 29×,
17× and 18× their run medians.

The freeze exception added **detection and recovery** — abort below 25% of the
running median, re-run once, exclude on a second stall — so it can no longer
be scored as a task failure. **It does not explain the stall.** Candidates not
yet separated: llama.cpp KV-cache behaviour, OS paging of worker pages,
thermal throttling, OneDrive I/O contention.

**Why deferred:** the measurement is protected. The diagnosis is a research
task in its own right.

---

## D3 — `httpx` timeout did not bound a blocking call

**Severity:** corrupting. Fixed incidentally, root cause not confirmed.

The 2539 s call carried `timeout=600.0` and ran unbounded. With
`stream: False` the read timeout measures the gap *between bytes*, and Ollama
sends nothing until it is done, so no timeout ever fires. The streaming
rewrite in the freeze exception makes wall-clock rules enforceable and closing
the stream actually stops generation.

**Not confirmed:** whether the same is true of every other blocking call path
in the codebase.

---

## D4 — de-hardcode brief items 1, 2 and 4

**Severity:** generality, not correctness for this iteration.

Only item 3 (the trap decomposer) was executed, as Phase 2B's instrument.
Item 2 (`coverage.py`'s `TOPICS` regexes) and items 1 and 4 remain.

Note on item 2's stated acceptance test — that coverage still yields 20/30
with the same per-tier split. That 20/30 was computed **by the regexes it
proposes to replace**, so it cannot serve as the acceptance criterion for the
replacement without assuming the answer. The existing README already records
that the heuristic under-counted: E06 and H25 were marked out of scope and
both lifted.

---

## D5 — The decomposer's confusion families are hand-written

**Severity:** generality. Disclosed rather than hidden.

`bench/decompose.py` derives candidate values from each task's own constants,
params and declared trap string, but the *families* of confusion (unit
multipliers, radius formation, body swaps) are a hand-written list.

**What makes it safe:** the guard, not the coverage. A task declaring no trap
gets no candidates and returns `unclassifiable`; matching requires 1e-6
relative agreement; a generic unit multiple of the expected value is *noted*
for a human but never *classified*. It can fail to name a trap. It cannot
invent one, and `tests/test_decomposer_does_not_invent.py` holds that line.

**Consequence to state wherever the distribution is quoted:** the
unclassifiable count is an upper bound on "not a designed trap", because some
of it is the tool's limited vocabulary.

---

## D6 — Invariant 11 rests on an uncalibrated estimator

**Severity:** corrupting, and larger than a reporting caveat.

`tokens.calibrate` has **no caller** (`KNOWN_INERT`), so `_CHARS_PER_TOKEN` is
never measured against a real tokenizer. Two consequences, and the second is
the one that was nearly missed:

1. **Reporting.** No token number in this project may be described as
   measured. They are estimates.
2. **Safety.** `enforce_ceiling` budgets against `usable_context_window`
   using those estimates. An uncalibrated estimator sits underneath a safety
   invariant: if the ratio is off, the ceiling fires at the wrong point —
   early, wasting context, or late, permitting the long-context degradation
   invariant 11 exists to prevent.

It also **cannot** run under `--profile local`: `count_tokens_exact` needs a
provider client and there is no API key.

---

## D7 — `bench/report.py` has no `machine_fault` / `excluded` column

**Severity:** cosmetic, but it hides a category.

`noise.py` now emits `machine_fault_keys` and `excluded_keys`. The run sheet
does not render them, so an excluded task is invisible in the rendered report
even though it is in the JSON. An excluded task must never read as a failure,
and right now it reads as nothing at all.

---

## D8 — Two `ollama serve` processes are running

**Severity:** cosmetic on this machine, a trap for the next one.

The project's dedicated instance on 11435 and the Ollama desktop app's own
server on 11434 both run. Nothing measured here touches 11434, but
`ORPHAN_NAMES` matching and any future "is the server up" check could see
either. The parentage fix means the reaper no longer kills live workers of
*either*, which is why this is not blocking.

---

## D9 — `--limit` semantics changed meaning under randomised order

**Severity:** cosmetic, documentation.

`--limit N` still takes the first N specs from the **suite table order** and
only then shuffles. The flag's own help text says "takes the FIRST N tasks,
which are all easy", which is no longer what a reader would infer now that
order is randomised. L6 used `--limit 10` and got a mix of easy and medium,
which is better, but by accident of the table rather than by design.

---

## D10 — User-facing stop paths do not route through `kill_tree`

**Severity:** corrupting, and it has now bitten four times.

A manual stop of the Phase 2A run did not reach the interpreter. Both
processes survived and the run kept appending to the ledger while it was
believed stopped:

    pid 33248  venv wrapper       python.exe bench/noise.py ... --resume
    pid 17264  real interpreter   child of 33248, same command line

This is SPEC-DELTA C13 again — a venv `python.exe` on Windows is a
trampoline, so anything that kills "the process" kills the wrapper and
leaves the interpreter running. `proc.kill_tree()` exists precisely for
this and handles it correctly; the problem is that the paths a *user*
reaches do not go through it.

**Occurrences:** the original `test_kill_and_resume` false pass; the
orphaned `llama-server`; the reaper's inability to tell a live child from
an orphan; and now a manual stop that silently did nothing.

**Why it is more than an annoyance:** a run believed stopped but still
executing holds the OS run-lock, keeps a model resident, and competes for
CPU with whatever is started next — which is exactly the contention that
manufactures `budget_exhausted` on a task that would otherwise finish.
Two measurement processes overlapping for 36 minutes is already on record
as having corrupted three tasks.

**Correct version:** every user-facing stop path routes through
`kill_tree()`, and a stop that cannot confirm the tree is gone reports
that rather than returning silently.

**Why deferred:** the freeze. It costs nothing to work around manually
(`kill_tree(<wrapper pid>)`), and no measurement currently in flight
depends on it.

---

## D11 — The wall-outlier detector has a warm-up blind spot

**Severity:** cosmetic, bounded to the first three tasks of a run.

The flag compares a task's wall clock against the running median of
completed tasks and needs at least three of them, so **tasks 1-3 of any
run can never be flagged**. In the Phase 2A run, M18 took 1777.4 s -
roughly 8x its neighbours - and passed unflagged as task 2 for exactly
this reason.

**Correct version:** seed the median from the previous run's tasks when a
ledger is being resumed, or fall back to an absolute ceiling until enough
samples exist.

**Note:** this is a gap in FLAGGING, not in the stall detector. A genuine
machine stall in the first three tasks is still caught by the tok/s test
and the transport timeout, both of which are independent of run position.

---

## D12 — `coverage.py` measures declared-trap coverage, not measured-trap incidence

**Severity:** corrupting, for any future use as a go/no-go signal.

Coverage predicted an upper bound of **20/30** tasks in scope for the
calibration skill. Phase 2B's decomposition found the model actually
commits a declared trap in **3** of 19 numeric failures.

The tool is not wrong about what it measures. It matches a skill's
vocabulary against each task's **declared** trap string, and it says so.
What it cannot know is whether the model **actually commits** that trap.

**Declared-trap coverage is not measured-trap incidence, and only the
second bounds a skill experiment.** Coverage answers "could this skill in
principle speak to this task" — a property of the suite and the skill.
Decomposition answers "did the model make the error the skill addresses"
— a property of the *model on that suite*, which is what a paired trial
actually measures.

**Consequence:** coverage's number is an upper bound on an upper bound,
and the gap between 20 and 3 is the distance between them. Its docstring
already says "treat a low number as a reason to stop and a high number as
permission to proceed, never as a prediction" — which is right, but
understates it. A high coverage number is not even weak evidence that an
experiment is possible.

**Correct version:** report coverage alongside measured-trap incidence
from a prior run whenever one exists, and never quote coverage alone as a
readiness signal.

**Why deferred:** the freeze, and the aerospace suite is being archived.
The limitation is recorded in FINDINGS 1.14 wherever the number is
quoted.

---

## D13 — The sandbox spawns `sys.executable`, which is fatal under any freeze

**Severity:** blocking, for packaging only. Harmless in every current
deployment.

`execute/sandbox.py` line ~239 spawns its child as
`[sys.executable, "-I", "-u", script]`. That is correct whenever
`sys.executable` is an interpreter and catastrophic when it is not: under
PyInstaller it is the frozen application, so each `run_sandboxed()` call
re-launches the app, which calls `run_sandboxed()` again.

Measured 2026-09-06 with a minimal frozen probe: eleven levels of nested
sandbox working directories and four chained `probe.exe` processes left
alive. Full detail and the three candidate fixes are in SPEC-DELTA C26.

**Why deferred rather than fixed:** nothing currently deployed is frozen. The
CLI, the tests, and the control plane all run from an interpreter and all
pass. Fixing it means either bundling a second interpreter or reworking the
spawn path, and neither is worth doing before there is a decision to ship a
desktop build.

**Do not attempt a desktop/Tauri build until this is resolved.** The failure
is not a broken feature, it is a fork bomb.

## D14 — PyInstaller onedir build is 169 MB

**Severity:** cosmetic, and only relevant if D13 is ever fixed.

The minimal probe — the agent package plus sympy, no web framework — froze to
169 MB onedir. The estimate for the packaged app was 25-30 MB. Most of the
weight is sympy and its dependencies.

**Correct version:** if a desktop build is ever revisited, measure onefile
(compressed) rather than onedir, and check whether sympy can be excluded from
the sidecar and required only in the sandbox's interpreter.

---

## D15 — An archived test writes `cal.json` into the repository root

**Severity:** cosmetic, but it re-dirties a clean tree on every test run.

`tests/archive/test_bench_suite.py` passes `--out` as `tmp_path / "cal.json"`
and a file nevertheless appears at the repo root. Verified by deletion and
re-run: removed at 01:00:56, back at 01:01:01, carrying the test's own
`skill_id: "abc"`.

`Config.resolve()` handles absolute paths correctly, and `cal.json` is not a
literal anywhere in `src/` or `bench/`, so one of the two `write_text` calls
in `bench/run.py` is writing to a path that is not `args.out`. Same shape as
SPEC-DELTA C9 — a path resolved against something other than what the caller
passed.

It survived this long because it looks like a leftover: the original file was
deleted as "a stray artifact of an aborted calibration" during housekeeping
and simply came back. It was then re-committed by a broad `git add -A`.

**Now:** untracked and in `.gitignore`, so it can no longer be committed by
accident.

**Correct version:** find which `write_text` ignores `args.out` and make it
use the resolved destination. Deferred because the freeze covers `bench/`,
nothing depends on the file, and it changes no measurement.

---

## D16 — Per-backend context windows are unreachable over the OpenAI protocol

**Severity:** would silently corrupt a measurement if the server is started
without `OLLAMA_CONTEXT_LENGTH`. Mitigated by both launchers setting it.

The OpenAI chat-completions API has no `num_ctx`, so every `openai`-kind
backend gets whatever window the server allocates. One server-wide value
cannot satisfy two backends declaring different `usable_context_window`s —
`config.openai.yaml` has a 3B asking 16384 and an 8B asking 8192 against the
same URL.

Budgeting BELOW the server's window merely wastes it. Budgeting ABOVE it is
the corrupting direction: M3.4's ceiling is computed from a number the server
will not honour, and Ollama truncates without an error.

**Why not detected in code:** the only portable signal is the returned
`prompt_tokens`, and flagging a mismatch means comparing it against this
project's own token count — which is an uncalibrated estimator (`calibrate`
has no caller; see the dead-code audit). A check whose false-alarm rate is
unknown is worse than a documented constraint, so this is documented.

**Correct version:** either query the server's window through whatever
non-portable route it offers and assert against the config at startup, or
calibrate the tokenizer first and then compare `prompt_tokens` to a count that
can be trusted. The second unblocks several other things and is the better
order.

SPEC-DELTA C29.

## D17 — `alpha verify` reports "head does not match anchor" as critical for tasks that were never re-checkpointed

**Found:** 2026-09-06, while confirming that Phase 2's search index does not
disturb the hash chain.

**What it is:** 19 of 283 tasks in `agent.local.db` report
`severity: critical — head does not match anchor`. The chain itself is fine.
Measured on a copy of the database:

    events before rebuild  : 7700
    events after  rebuild  : 7700
    heads changed          : 0
    chain breaks           : 0
    tasks never anchored   : 2 of 283
    anchored but mismatched: 19

So the chain verifies for every task and nothing is tampered. Those 19 tasks
simply continued past their last `checkpoint()` — bench runs do not checkpoint
per task — and the anchor file is behind, not wrong.

**Why it matters:** the finding is emitted at `severity: critical`, the same
level as an actual chain break. 19 permanent criticals in `alpha verify` is
how a real one gets scrolled past. This is the project's own recurring defect
class — a check whose label does not match what it measures — in the alarm
that matters most.

**Why deferred:** pre-existing, unrelated to any phase, and the freeze holds
for anything a phase does not need. It also touches the checkpoint protocol,
which is not something to change while corrections are landing around it.

**The fix, when it happens:** separate "chain broken" from "anchor is behind".
The second is `severity: info` at most, or is not reported at all for a task
whose head is a descendant of the anchored hash.

**Confirmed not caused by the search index:** the numbers above are before and
after a full `rebuild()`.

**RESOLVED 2026-09-06**, unfrozen because Phase 3 puts memory files under this
same chain and a memory-tampering alert would have arrived as the twentieth
critical in a list nobody reads. `classify_anchor()` separates the two
conditions by asking whether the anchored hash is STILL IN the chain: present
means the chain extended past the checkpoint and nothing was rewritten
(`info`), absent means the history containing it is gone (`critical`). The
live database now reports 0 critical and 19 info.

The downgrade can only ever apply to an anchored hash that is provably still
in the log, so a tampered chain cannot reach the quieter branch — tampering is
what removes the hash that would qualify it. Verified by injection rather than
by reading the logic: `tests/test_anchor_behind_is_not_tampering.py` excises
the anchored event and rebuilds a different tail, and the finding stays
critical.

**D17 was the SYMPTOM. D10 is the disease** — manually stopped runs, and bench
runs generally, never call `checkpoint()`, which is why 19 tasks drifted past
their anchors in the first place. D10 remains frozen and open. Reclassifying
the alarm does not checkpoint anything; it stops a bookkeeping gap from
occupying the channel reserved for tampering.

## D18 — `isolate_retrieval` has no equivalent for the ladder and calibration paths

**Found:** 2026-09-06, while closing the cross-arm retrieval vector.

`bench/noise.py` turns retrieval isolation on for itself. `bench/ladder.py`
and the calibration path build their own contexts and do not. They do not
currently call `search_history` or `expand_artifact` in a way that could leak
— the ladder measures scaffolding rungs on single tasks — so nothing is
contaminated today.

**Why it is logged rather than fixed:** the freeze holds, and the right fix is
probably not three call sites setting a flag but one place that refuses to
run a measurement without declaring an isolation state. That is a design
change, and it belongs with Phase 5 (tool exposure), which introduces the
machinery for restricting what a run can see.

**What would make it urgent:** any measurement path gaining a retrieval tool,
or `search_history` entering the default toolset for a ladder run.

## D19 — skill provenance cannot distinguish "no sources" from "retrieval broke"

**Found:** 2026-09-07, while making `web_search` report parse failures
separately from empty results.

`generate_skill` grounds on `ctx.research_retrieve(pattern.description, k=5)`
and drafts from whatever comes back. If the scraper's markup assumptions
break, retrieval returns nothing, and the skill is drafted from model priors
while its provenance records an empty source list — **an ungrounded skill
that looks grounded.**

The tool layer now distinguishes the two (FINDINGS 1.19), but
`research_retrieve` flattens it: it collects `Source` objects and returns a
list, so "no sources exist" and "retrieval malfunctioned" are both `[]`.

**Why deferred rather than fixed:** no skill has been generated yet — M11 is
unbuilt by design, the gate was built first — so nothing is currently
mis-provenanced. The fix belongs with Phase 12, where portable skill
provenance is the subject and the format has to carry the distinction anyway.

**What would make it urgent:** the first real `generate_skill` run. It must
not happen before this is closed, because a skill promoted on the strength of
provenance that could not tell those apart is exactly the failure the gate
exists to prevent.

## D20 — the resume protocol is inert: resuming a task does not verify its chain

**Found:** 2026-09-07, by the dead-code audit, immediately after it was
tightened to stop crediting method calls as references to module-level
functions.

`kernel/integrity.py:resume()` is M1.4/M1.5. It verifies the hash chain,
appends `integrity.halt` and raises on a break, then compares head against
anchor — all before a task is allowed to continue. **Nothing imports it.**
`cli.py`, `control/server.py`, `execute/loop.py` and `plan/planner.py` each
import other names from that module and never this one.

So a paused task that is resumed goes straight into `run_task` without its
chain being verified. The guarantee is written, tested, and never invoked —
the same category as `record_holdout_gap`, and the reason
`test_wired_invariants.py` exists.

**Why it hid for so long.** Every textual search for it found something: the
`--resume` CLI flag on `bench/noise.py` (ledger-row resumption, an unrelated
mechanism), the phrase in `runlock.py`'s docstring, and the module's own
docstring. The audit credited them all until the reference rule was made
receiver-aware.

**Noted against my own work:** the D17 fix two commits earlier modified
`resume()` to stop refusing a merely-behind anchor. That change is correct
and currently unreachable. The `verify_all` half of D17 IS wired — `alpha
verify` and the `/alerts` endpoint both call it — so the reclassification is
live; only the resume half is inert.

**Why deferred rather than fixed:** wiring it is a design decision, not a
line. There is no distinct "resume a paused task" entry point — `run_task`
is called the same way for a fresh task and a resumed one, so the fix is
either a check inside `run_task` that knows whether it is resuming, or a
separate entry point. Choosing between those is Phase-shaped work and the
freeze holds.

**What would make it urgent:** any workflow that routinely pauses and
resumes. Today paused tasks are resumed by hand, and `alpha verify` sweeps
every chain on demand, so the exposure is a task resumed without anyone
running that sweep first.

**RESOLVED 2026-09-07.** `resume()` is called from the top of `run_task`,
before `set_status`, so every one of the nine call sites inherits it and the
two future ones (a channel reviving a parked task, a scheduled wake) will
too. Put there rather than at the three continuation call sites because a
door each caller must remember is a door someone forgets — and retrofitting
onto three entry points is what the Phase 8 and 11 work would have made five.

A fresh task passes trivially: an empty chain verifies and has no anchor, so
there is no fresh-versus-resumed branch. That absence is deliberate — a check
that only runs on the resumed path is one nobody exercises until the day it
matters.

**No published number came from an unverified resume, verified rather than
inferred.** Across all 283 tasks in the live database, ZERO entered `RUNNING`
more than once. `set_status(RUNNING)` is the first thing `run_task` does, so
a second call on the same task id always leaves a second transition; the
absence of any is proof no task was ever continued. `bench/noise.py --resume`
creates NEW tasks for the remaining specs rather than continuing old ones,
which is both why the 2A run was safe and why the word "resume" made the dead
function look wired.

**Wiring it exposed a live defect it had been hiding: D21.**

## D21 — RESOLVED ON SIGHT: two kinds of record shared one anchor key

**Found:** 2026-09-07, the moment `resume()` was wired and the test suite ran.

`checkpoint()` wrote a chain head to the anchor under the task id.
`approve_plan()` wrote `plan:<hash>` under the SAME task id. The anchor file
is an append-only (time, key, value) log, so whichever was written last won.

This broke both readers, in opposite directions:

* **The resume check** read a `plan:` value, could not find it among the
  task's event hashes, and concluded the history had been rewritten. Any task
  with an approved plan would have halted on resume.
* **`verify_plan_pin`** read a chain head, found it did not start with
  `plan:`, and silently skipped its own anchor check. Since `run_task`
  checkpoints on every run, the plan pin stopped being the latest entry after
  the first step — so **that half of plan verification has effectively never
  fired.**

The first is why the whole thing stayed hidden: the inert function was also
broken, and nothing called it, so neither fact could surface. It took wiring
one to reveal the other.

**Fixed, not deferred**, because it was blocking D20 rather than adjacent to
it. Plan pins now use their own key. `Anchor.latest_chain()` returns only
values that look like a chain head — 64 hex characters — so existing anchor
files keep working without migrating an append-only log.

**A third instance of DR1.** Not a scope mismatch this time but a NAMESPACE
one: two kinds of record in a single keyspace, with the same consequence — a
control that reports on something other than what it is guarding.

## D22 — the dead-code audit never looks at methods, and M7.4 rule 3 is inert

**Found:** 2026-09-07, by re-running the improved audit against what it had
been blind to rather than only against the case that prompted the fix.

`_unreferenced()` iterates `ast.parse(f).body`, so it sees module-level
functions and **no methods at all**. A one-off sweep of the 128 public
methods in `src/agent` found 14 with no production reference. Most are
explainable:

* `_Text.handle_data` / `handle_starttag` / `handle_endtag` — `HTMLParser`
  callbacks, invoked by the stdlib. False positives.
* `AccessGuard.dispatch` — ASGI middleware, invoked by the framework.
* `ExternalMemoryProvider.recall` / `remember` and the `NoExternalProvider`
  pair — tier 3 is an interface with no implementation by design (A1 3.5).
* `HoldoutRegistry.contains_any` — test-only, and its docstring says so.
* `Anchor.all_tasks`, `BackendBase.set_throughput`, `ToolRegistry.read_only`,
  `SkillStore.by_status`, `EventLog.last_of_type`,
  `AgentContext.gate_queue` — small helpers with no caller.

**One is not explainable, and it is the reason this entry exists.**

`VerificationMesh.verify_isomorphic` is **M7.4 rule 3** — verify the same
property two independent ways, and treat disagreement as a finding rather
than averaging it away. It has a test (`test_m7_assure.py:119`) and **no
production call site.** So no verification in this project has ever been
cross-checked against a second verifier, and "isomorphic disagreement" has
never been able to appear.

Same category as `record_holdout_gap`: an inert guarantee, tested into
looking alive. The audit's own principle applies — *a test that a function
works is not a test that the system calls it* — and the audit could not
apply it here because it does not read methods.

**Why deferred rather than fixed:** two separate pieces of work. Extending
the audit to methods needs the framework-callback false positives handled
(decorator-less ASGI and HTMLParser hooks look identical to dead code from
the AST), and wiring rule 3 is a design question about which verifier pairs
are genuinely isomorphic for a given task — not a line. The freeze holds and
Phase 10 is queued.

**The sweep above is the half of the work already done**, recorded so whoever
takes it does not repeat it.

## D23 — RESOLVED ON SIGHT: an arm could not read an artifact another arm wrote first

**Found:** 2026-09-07, by the DR1 keyspace sweep (I2), in code I added in
Phase 5.

`artifacts.id` is `sha256(content)[:32]` and rows are written with
`INSERT OR IGNORE`, so two tasks producing identical bytes share ONE row, and
that row keeps the FIRST writer's `task_id`. The Phase 5 scope check on
`expand_artifact` asked "does the row's task_id fall in the visible set",
which answers a different question from "did this task produce it".

Not hypothetical. Both arms of a paired trial run `list_dir` against an
identically-reset workspace and get byte-identical output, so the second arm
was refused access to **its own artifact**.

**Why that is worse than an inconvenience:** it is arm ASYMMETRY produced by
the rule meant to prevent cross-arm contamination. One arm could expand its
tool output and the other could not — a difference between the arms that has
nothing to do with the skill under test, which is exactly the property a
paired trial assumes away.

**Fixed rather than deferred**, because it was live, mine, and silently
degrading the measurement the project exists for. Authorship is now read
from the event log: `artifact.written` is appended per task, on the hash
chain, carrying the id. The chain already recorded the right thing; the row
never could.

**A fourth DR1 instance**, and the one the design rule predicted: a
content-addressed keyspace shared across tasks with an owner column that
records only the first writer.

## D24 — two tasks cannot approve identical plans

**Found:** 2026-09-07, by the same sweep. Not fixed.

`plans.id` is the content hash of the plan body, and it is the PRIMARY KEY.
The body contains no task id, so two tasks with the same objective and steps
produce the same id and the second `INSERT` raises
`UNIQUE constraint failed: plans.id`.

**It fails LOUDLY**, which is why this is logged rather than fixed — a crash
at approval time is recoverable and visible, where the artifact version of
the same collision was silent and corrupting.

**Not currently reachable in any measurement path:** `config.local.yaml` sets
`plan_required: false` and no bench entry point drafts plans. It becomes
reachable the moment a paired trial runs with planning on, where both arms
would draft the same plan for the same task and the second approval would
crash.

**The fix when it is needed** is the same shape as D21 and D23: key the row
by what it belongs to, not only by what it contains — `(id, task_id)` rather
than `id` alone.

## D25 — the Anthropic adapter accepts `on_chunk` and ignores it

**Found:** 2026-09-07, while wiring B5 through every adapter.

`AnthropicBackend.complete` takes the `on_chunk` argument so the `Backend`
protocol stays honest, and does nothing with it: that adapter does not stream,
and making it stream means rewriting a request path that has NEVER MADE A LIVE
CALL. Its module docstring already carries the UNVERIFIED banner.

**Consequence:** a `--profile mixed` run shows nothing on any surface until a
turn completes. Local runs — which is every run this project has done — are
unaffected.

**Why deferred:** rewriting an unexercised path to add a feature nobody can
test is how an adapter acquires a second unverified claim. It should be done
in the same session as the first real Anthropic call, so the streaming path
and the request shape are verified together.

**What would make it urgent:** any surface work aimed at a cloud tier, or the
G1 measurement, which needs a paid backend anyway.

**CLOSED 2026-09-13.** The adapter now streams through `messages.stream()`,
forwarding text deltas to `on_chunk`, on ONE path - which also removes a defect
found on the way (C46: the SDK refuses a large non-streaming request outright).
Tested against a fake client only; there is no key. Verification is D26's now.

## D26 — the Telegram transport has never made a live call

**Found:** 2026-09-08, by instruction. Phase 8 carries a STOP before a real
bot token.

`HttpTransport` is written and unexercised. Everything above it — the
allowlist, the drop path, task creation, the park, the buttons, the callback,
the re-verification, the finality guard — is driven end to end through
`FakeTransport`, so the LOGIC is tested and the four HTTP calls are not.

**Consequence:** the shape of Telegram's `getUpdates` response, the exact
`inline_keyboard` payload, and the callback_data round trip are asserted
against my reading of the API rather than against the API. Same standing as
the Anthropic adapter (D25), marked the same way in the module docstring.

**Why deferred:** running it needs a real bot token, which is the user's
decision, not mine.

**What closes it:** one session with a token and a private chat. Check first
that `getUpdates` returns `{"ok": true, "result": [...]}` with `update_id`
where this expects it, and that a pressed button delivers `callback_query.data`
unchanged. Those two are the only places a wrong guess is silent rather than
loud.

## D27 — one task at a time, and the bot has no queue

**Found:** 2026-09-08, building A4.1.

`serve_forever` runs a task inline before polling again, so a second message
arriving during a run waits for the poll rather than being queued. The chain
lock is process-wide and single-writer by design (`kernel/db.py`), so
concurrent runs are not available anyway.

**Consequence:** two allowlisted people using the bot at once get serialised,
with no acknowledgement that they are waiting. Acceptable for a personal bot
with a short allowlist; not acceptable for anything wider.

**Why deferred:** a queue is not the fix — the single-writer chain lock is the
real constraint, and the honest version of this is the claim-then-execute
receipt protocol already described in `kernel/db.py`. Building a queue in
front of a lock that serialises anyway would look like concurrency and deliver
none.

**D26 now holds TWO unexercised live paths, 2026-09-13**, by instruction: the
Telegram transport, and the Anthropic adapter's streaming path (D25, closed as
a gap, open as a claim). They close together whenever a token and a key appear.
The Anthropic half needs one trivial `--profile mixed` task: confirm tokens
reach a surface while the turn is running, that `cache_read_input_tokens` is
non-zero on the second call, and that a refusal is reported rather than
rerouted.

## D28 — a resumed task does not see its own history

**Found:** 2026-09-13, while tracing why the taint tracker's lifetime gap was
latent rather than live.

`run_task` seeds a resumed run with the objective, the plan, and (since C43)
the released decisions. Every observation from before the park is gone from
the model's context - it is in the artifacts and on the chain, and the model
can reach it with `expand_artifact` or `search_history`, but it is not in the
tail. A task resumed after approval effectively starts over with one fact.

**Why deferred:** restoring the tail is a harness change for every resumed task,
and deciding what to restore - all of it, the last session's seed, a
projection - is a design question that Phase 9's sessions make answerable but
do not answer.

## D29 — an EMPTY artifact_scope allows every path (CLOSED 2026-09-13: empty means nothing, C53)

**Found:** 2026-09-13, by the storage-layer sweep.

`within_scope` returns allowed when `artifact_scope` is empty. `TaskContract`
defaults to an empty scope. So a task that declares nothing may read any file
the process can - `~/.ssh` included - with only the protected-path list in the
way. That is the fail-open shape Phase 8 refused for the channel allowlist,
where empty means NOBODY.

**Why not fixed here:** it is the default for every contract, and changing what
"no scope" means changes behaviour for every task that relied on it. That is
the user's decision. C45 closed the part of it that touched this project's own
state.

**Closed 2026-09-13, by that decision.** Empty means nothing: `within_scope`
refuses every path-shaped argument when no scope is declared and says what to
declare; `alpha new` and `POST /tasks` warn at creation. The sibling sweep is
design rule DR4.

## D30 — Phase 9's summarizer: a design STOP (CLOSED: 9.2 withdrawn, C49)

**Found:** 2026-09-13. See SPEC-DELTA C47.

The tail holds only observations, so there is no conversation history for 9.2
to summarize that is not tool output, which 9.1 forbids summarizing. Three ways
forward, each a decision rather than an implementation detail:

1. **Summarize tool output anyway.** Contradicts 9.1 and the 17/17 evidence.
   Not recommended.
2. **Put the model's own turns into the tail first**, then summarize those.
   Every multi-step task would then see its own previous outputs, which it has
   never done: a harness change for every measured task, so a HARNESS_VERSION
   bump and its own STOP - and arguably a bigger change than the summarizer.
3. **Treat a session as a conversation SPANNING tasks** - a Telegram chat, a
   TUI session - where conversation history is the prior (goal, answer)
   exchanges. That is real conversation, trajectories stay untouched, and
   benchmark tasks, being single exchanges, never compress at all.

Recommendation: 3. It is the only reading under which 9.1 and 9.2 are both
satisfiable without a harness change, and it is where unbounded growth
actually happens: a task ends at 24 steps, a chat does not.

## D31 — the Anthropic path has no stall detection

**Found:** 2026-09-13, writing D25.

`StallWatch` protects the OpenAI-protocol adapters. The Anthropic adapter never
had it, streaming or not. Wiring it is small, but it would be the second
unverified behaviour added to a path that has made no live call; it belongs in
the same session as D26's verification.

**D30 CLOSED 2026-09-13.** Decided by instruction: 9.2 is withdrawn, not
deferred. The child session is seeded by reference. See SPEC-DELTA C49.

## D32 — the sandbox is a door, not a wall

**Found:** 2026-09-13, Item 2's sweep (C50).

The sandbox's audit hook now refuses reads, sqlite connections, listings and
process starts against protected state. It lives in the process it polices, so
code written to defeat it - `ctypes`, a native extension - can. It stops a model
that REACHES for a protected file; it does not stop one that is TRYING.

**What closes it:** Phase 6 (A6, a container per run), parked because Docker is
not installed. Until then, isolation holds by construction for every tool route
and by a guard for code execution, and the run record should say so.

**SQLite, 2026-09-14 (C55).** The fence reads a database string as SQLite
reads it and authorizes ATTACH; the authorizer is installed by a wrapped
`sqlite3.connect`. A `Connection` built another way, or
`set_authorizer(None)`, gets past it - the same door.

## D33 - tier 1 sits before ~966 tokens of tool schemas on this template

**Found:** 2026-09-13, by 9.6 (SPEC-DELTA C48).

The prefix renders tier 1 inside the system text. The local template appends the
tool schemas after the system text, so any tier-1 change invalidates the whole
schema block: the boundary took 77 s with a memory change against 36 s without.
Anthropic renders tools BEFORE system, where the same change would cost only the
system block, so the layout that is cheap depends on the provider.

**Why not changed:** moving tier 1 out of the system text changes prefix
STRUCTURE, which this project measured moving pass rates (the harness tax). That
is a HARNESS_VERSION change and needs its own STOP.

**Decision, 2026-09-13: not moved.** A memory change becomes visible only at a
session boundary, so the rebuild is paid rarely. Moving tier 1 needs a
`HARNESS_VERSION` bump, and a bump makes every published number incomparable
with every later one. A rare cost does not buy a certain one.

**Revisit when memory-change frequency rises past one threshold: a boundary
rebuild more than once per task on average.** Count `session.opened` rows whose
`memory_sha256` differs from their parent session's, and divide by tasks. The
measurement record predates sessions (283 tasks, 0 `session.opened` rows), so
the rate is unobservable there; the first run that crosses a boundary
produces the first number.

## D34 - the supervisor and a measurement can share the machine (CLOSED 2026-09-13: the holder is published, C54)

**Found:** 2026-09-13, building 11.

A scheduled task that fires during a measurement competes for the same CPU,
and wall clock is this project's cost axis - the run lock exists because
contention can manufacture `budget_exhausted` on a task that would finish. The
supervisor does not consult the lock, because the lock cannot be probed without
being taken (C52). Until a non-intrusive signal exists - a measurement writing
a marker the supervisor reads, say - do not leave `alpha supervise` running
through a measurement, and the run record cannot yet prove it was not.

**Closed 2026-09-13 (C54).** The signal was fixed, not the behaviour: the
holder publishes itself beside the lock and anyone can read it without taking
anything; a dead holder is recognised by PID. The supervisor then refuses to
fire or run while a live holder exists, and logs why. One lock per project,
whatever `--out` says.

## D35 - the calibration's positive control cannot declare a truthful drafting set (CLOSED 2026-09-15: option (a))

**Found:** 2026-09-13, by D29's sweep (C53).

`aero_units_discipline` was hand-written by an author who saw the whole
`aero_calc` suite, so its truthful drafting set is every task in the suite -
and under the fail-closed contamination check that refuses every trial. The
calibration, a POSITIVE CONTROL on the detector that is contaminated by design
and caveated as such, cannot run on a true record; declaring `none` would let it
run on a false one.

**Why not decided here:** it is the measurement plan's call. Options: (a)
declare the whole suite, and write the positive control's skill blind to the
suite; (b) an explicit `positive_control` declaration that permits trials but
makes the skill unpromotable, so its verdict can only ever calibrate the
detector; (c) something else. **Recommendation: (b)** - it states the
contamination instead of hiding it, and enforces the one consequence that
matters. Until decided, `bench/run.py paired` refuses this skill, `--resume` of
the 10-pair run of 2026-08-27 included.

**Closed 2026-09-15: option (a), not the recommendation.** There is no
`positive_control` category. The control is a new skill,
`si_first_orbital_arithmetic` (`a4aa91a3e464`). It was written from the
drafting split only, declares that split, and is refused on every drafting
task: the rules every skill follows. Its provenance also records what cannot
be ruled out: the drafting model worked on this project in earlier sessions,
and that context is not retained. `aero_units_discipline` declares the whole
suite, truthfully, and is refused on every task. It is kept as a record, not
made runnable. The report's caveat, which assumed "the author saw the entire
suite" for every skill, is now read from each skill's provenance (invariant 107).

## D36 - a model call that never returns has no `calls` row (CLOSED 2026-09-15: the row is written at call time, C59)

**Found:** 2026-09-14, by A2-2.

Invariant 12 says every model call is recorded in `calls`. The record has
1,214 `model.called` and 1,183 rows: the 32 calls that never returned - 17 cut
off by a killed process, 12 stalled, 3 crashed - have none, because the row is
written from the response's usage. Their wall time is in the task's budget
events and missing from `calls`, so any per-call timing drawn from `calls`
silently drops exactly the slowest calls.

**Fix shape, not built:** write the row at call time with null usage, and
complete it on return. Changes what `calls` means for every existing
analysis, so it waits for a decision.

**Closed 2026-09-15, by that decision, in that shape.** Every task-bound call
goes through `AgentContext.call_model` (invariants 104-106). Building it found
a second gap. Five call sites never wrote a row at all, and one of them, the
deputy, ran 15 times in the record. The migration was checked on a snapshot of
`agent.local.db` first: 1,183 rows identical. No published headline figure
moves: CHANGELOG 2026-09-15 has the table.

## D26 addendum, 2026-09-14 - the tests are waiting

Both live paths now have a test that runs the moment the credential exists:
`tests/test_live_credentials.py::test_telegram_transport_live`
(`ALPHA_TELEGRAM_TOKEN`) and `::test_anthropic_streaming_live`
(`ANTHROPIC_API_KEY`, one small paid call per Anthropic backend). Both skip
without it. The same bodies run every suite against local fakes. The STOP is
unchanged: supplying either credential is the user's decision.

## D37 - resource limits are POSIX-only; on Windows the sandbox enforces none

**Found:** 2026-09-14, by A2-2 (FINDINGS 1.25). **Stated 2026-09-15** in
README and RELEASE.

`sandbox.py` sets `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_NPROC` and `RLIMIT_FSIZE`
when `os.name == "posix"`. Every run in this project's history was on Windows,
where none of them applies. There, the sandbox is advisory path confinement
plus a wall-clock timeout.

**Recommendation: a Job Object, before any unattended use.** It would set:
- memory, with `JOB_OBJECT_LIMIT_PROCESS_MEMORY`;
- CPU time, with `JOB_OBJECT_LIMIT_PROCESS_TIME`;
- active processes, with `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`;
- `KILL_ON_JOB_CLOSE`, which also takes the tree down when the parent dies (a
  sibling of D10).

It needs only `ctypes`, no new dependency.

**Effort: about a day.** The code is about a hundred lines; the rest is the
assignment race. `Popen` does not hand back a suspended child's thread, and the
venv `python.exe` is a trampoline that spawns the real interpreter (C13). So
the job must be in place before the trampoline's child exists. Each limit can
be tested for real on this machine. File size has no per-process Windows
equivalent and needs Phase 6.

A limit that is hit becomes a kill, which `run_code` can observe, so this
likely bumps TOOLSET_VERSION. It does not bump HARNESS_VERSION.

**Why not built now:** no new features (2026-09-15). Measurement runs are
attended and bounded by the wall clock. Cron and the channel are neither.

## D38 - `max_taint_in`: decided DELETE, blocked on a HARNESS_VERSION STOP

**Found:** 2026-09-14, by A2-2. **Decided 2026-09-15: delete** (SPEC-DELTA C60).

The field cannot be wired to mean anything. Every observation tool is
UNTRUSTED, so a ceiling below the default forbids reading, and the default
permits everything. But the whole contract is rendered into every prefix, so
removing the field changes every task's prefix bytes. Options:

1. **Delete it and bump HARNESS_VERSION 2 → 3.** Every published number becomes
   incomparable with every later one, for a field that changes no behaviour -
   unless the bump is batched with one that has to happen anyway (D33's tier-1
   move, D28's tail).
2. **Remove it from the contract and code, but keep its bytes in the prefix**
   as a labelled legacy constant until the next bump. The prompt would still
   show the model a ceiling nothing enforces.

Recommendation: (1), batched with the next bump. Until then it is inert, and
documented as inert.
