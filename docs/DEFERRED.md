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
