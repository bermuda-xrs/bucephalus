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
