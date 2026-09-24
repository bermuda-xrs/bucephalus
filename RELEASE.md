# Release checklist — what "first iteration complete" means

This project's deliverable is **a measurement, plus an instrument honest
enough that the measurement can be checked**. So "done" is not "the code
runs"; the code has run for weeks. Done is: a number, with its noise floor,
its calibration, its profile, and its caveats attached.

Nothing below is a formality. Each line is something that has actually gone
wrong here at least once.

---

## 1. The instrument

- [x] `setup.ps1` completes and the suite is green (223 tests)
- [x] `demo.py` runs end to end with no network, refuses an injected
      instruction, and reports a verifying hash chain
      *(this box was FALSE from 2026-09-06 to 2026-09-16: the demo did all
      the work and then died formatting an unmeasured cache ratio. Fixed, and
      now run by a test - C67, invariant 113, FINDINGS 1.28)*
- [x] Every benchmark answer independently recomputed — no expected value
      exists only as a table entry (`tests/test_bench_suite.py`)
- [x] Every verifier rejects a deliberately wrong answer. A suite whose
      checks cannot fail measures nothing; an audit found this in ~59% of
      SWE-bench Verified's hardest tasks
- [x] Every arm of every run starts from identical bytes on disk
      (`src/agent/workspace.py`, invariant 28)
- [x] Long runs are resumable and refuse to blend across a changed suite,
      split, or profile
- [x] The gate's binding conditions are pinned by tests, not just comments
      (`min_discordant_pairs` guards; `min_lift_pp` is inert below n=160)
- [x] Both long paths dry-run tested on scripted outcomes before spending
      hours (`tests/test_calibration_path.py`)

## 2. The measurement

- [ ] **Noise floor**, all 30 tasks × 3 baseline runs. Classify each task
      3/3, 0/3, or flaky. **More than 8 of 30 flaky → stop and tighten task
      specs**; a lift measured against that much noise cannot be interpreted
- [ ] **Calibration**, the hand-written skill through the full gate on all 30
      as a positive control. **If it is rejected, the instrument is broken
      and every later rejection is uninterpretable.** A gate that rejects
      everything and a gate that works are indistinguishable until this
      passes
- [ ] Run sheet generated (`bench/report.py`) with profile, models, date and
      caveats attached to the numbers rather than footnoted

## 3. The claim

State it in this shape, or not at all:

> Skills that pass a paired-trial gate raise a local model's task success by
> **X points** at **Y%** of the cost of a frontier model, measured on **N**
> tasks, on **DATE**, model **VERSION**, profile **local**.

- [~] Reported three ways — the run sheet renders the table and **names the bare-model arm as never measured**, which bounds the claim to "a skill moves the scaffolded model". Running that arm is still outstanding
- [x] Contamination caveats in the **same paragraph** as the number — enforced by `bench/report.py` and asserted by a test that scans the rendered line
- [x] Anything unverified is called unverified. The Anthropic adapter's module docstring carries `UNVERIFIED ... NEVER been exercised`

## 4. Packaging — known state

- [x] A wheel builds at this path. The Greek-character problem affects only
      *editable* installs, which write a `.pth` in the system ANSI codepage
- [x] **Build it with `pip wheel . --no-deps -w dist`.** `python -m build`
      fails here: setuptools' own `build/` output directory shadows the
      `build` module, and the resulting "No module named build.__main__"
      reads like a missing dependency rather than the shadowing it is.
      SPEC-DELTA C27
- [x] **The wheel carries its runtime assets.** `themes/*.toml`,
      `assets/*.ans` and `control/*.html` ship as package data. Verified
      2026-09-06 by installing into a clean venv in another directory:
      `postinstall_check.py` runs a task end to end, state resolves to the
      user data dir, and nothing is written to cwd.
      `control/*.html` was **missing until 2026-09-06** — a pip-installed
      control plane served its whole JSON API and raised `FileNotFoundError`
      on `GET /`, the one route a human opens. SPEC-DELTA C24
- [ ] **`policy.md` and the config files are still not shipped**, and the
      runtime resolves them relative to the config's parent. So an installed
      wheel runs the library but still needs a checkout for its
      configuration. Making it fully standalone means shipping those too, or
      an `alpha init` that materialises them into a working directory
- [x] **LICENSE.** Apache 2.0 is in the tree. This entry read "No LICENSE,
      deliberate" long after the file was added — found 2026-09-17 while
      rewriting the handover, and the fourth instance of the stale-document
      class
- [x] `CHANGELOG.md` reflects what actually changed, including what is still
      undone

## 5. Before tagging

- [ ] `git log --oneline` reads as the project's memory — no commit message
      claims more than was verified
- [ ] `docs/FINDINGS.md` 1.25 (what has never run for real) matches reality, and
      `docs/HANDOVER.md` passes `tests/test_handover_is_current.py`
- [ ] `runs/` artifacts worth keeping copied into `docs/evidence/`
- [x] The version in `pyproject.toml` and `src/agent/__init__.py` agree (both 0.1.0)

## 6. Running a measurement, operationally

Learned the hard way, each after it went wrong:

- Drive every long run through `.\measure.ps1 next`. It runs detached
  (session teardown kills foreground jobs mid-run), refuses to start while
  another holds the lock, and refuses to chain past a decision gate.
- **Never start a second measurement.** Two overlapping passes measured three
  tasks under 2x CPU load, and `budget_exhausted` is a time limit, so
  contention can manufacture that outcome. `src/agent/runlock.py` now makes
  this impossible rather than discouraged.
- Nothing else on the machine during a pass. The same task takes 162s idle
  and 339-360s with a test suite alongside.
- `work/` sits under OneDrive, whose sync client holds handles. The workspace
  reset deletes files rather than directories because of it. Excluding
  `work/` from sync would remove the whole class of problem.
- `.\measure.ps1 status` is the source of truth for what may run next; the
  gates live in `bench/nextstep.py`, not in anyone's memory.

---

## The rule that outranks the rest

If a result is disappointing, publish it anyway with the caveats intact. The
literature says self-generated skills average about **−1 point**. The gate
exists so that failing is *visible* rather than silent, and a believable
number with its flaws disclosed beats an impressive one nobody can check.

---

# ITERATION TWO: COMPLETE — 2026-09-16

**The phase program is finished.** Phases 1-5 and 7-11 shipped. Phase 6
(Docker) and Phase 12 (portable provenance) are **withdrawn by decision**, not
deferred - FINDINGS 1.29 carries the reasoning for both, so neither reads
later as a missing feature.

Iteration two set out to finish the runtime and keep the instrument honest. It
did both, and the second cost more than the first: **five of this iteration's
findings are defects in the measuring apparatus, not in the agent.**

## Against the eight goals

| Goal | Where it stands after iteration two |
|---|---|
| **G1** ≥10× reduction in paid tokens | **CLOSED 2026-09-25: not supported on this executor.** Stated per mechanism as saving-when-it-fires x fire-rate (FINDINGS 1.35): compaction ~24% / 0 real crossings in 254 steps; semantic cache 0 executor tokens / 0 compressions; prefix stability one re-prefill per write / not measurable under isolation; code-as-action ~77% when used / used on 1 task type - and removing it raised the pass rate 0/5 -> 2/5 in each of 3 runs (n=3). What would change it: a model that reads before answering. No paid call has been made (D26). Every earlier G1 number is void (v3, v4, v5) |
| **G2** Mobile remote control | **partially implemented.** The HTTP control plane, auth and SSE exist; Phase 8 added a Telegram channel with an allowlist, approvals and a park path, driven end to end against a fake transport. No live request has ever been made (D26) |
| **G3** Multi-agent as context isolation | **implemented, unmeasured.** Delegation, the five gates and a narrowed child policy exist; no production task has delegated |
| **G4** Research-grounded, verified skill generation | **the gate works; the generation half is RETIRED UNANSWERED** (FINDINGS 1.27). The published baseline is -1.3 pp, the suite that would evaluate it is archived, D19 was never closed, and none of its three components ever ran |
| **G5** Least-privilege security with provenance | **implemented and measured.** This iteration added the file-route and SQLite fences (C50, C55), per-write taint rebuilt from the record, tool exposure, and D37's Job Object. The gate's refusal path has still never fired in production |
| **G6** Durable, resumable, interruptible execution | **implemented and measured.** `integrity.resume` was found inert and wired (D20); a resumed task now also sees the tail it had when it parked and the ids of everything it observed (D28). The one user-facing stop path routes through `kill_tree` (D10) |
| **G7** Self-measuring, eval harness first-class | **implemented and measured - after being repaired twice.** `calls` now records a call from the moment it starts (D36); the noise floor could not start for ten days and does again (C64); the run sheet no longer hides excluded tasks (D7) |
| **G8** Portable skill format | **implemented; the boundary crossing is demonstrated.** A skill authored elsewhere enters as an untrusted candidate, keeps its bytes, and is gated like anything else. No command imports one, so the import path stays inert by design |

## What iteration two actually produced

- **The runtime finished:** the OpenAI-protocol adapter, FTS5 search, persistent
  memory, web and file tools, tool exposure, streaming and a terminal surface,
  parallel tool execution, a Telegram channel, sessions, and cron.
- **790 tests, 3 skipped** (the skips are the two credential-gated live paths).
- **An instrument that now tests its own front doors.** 1 of 13 entry points
  had a test that invoked it; `demo.py` and the noise floor were both dead for
  ten days, killed by one commit (FINDINGS 1.28).
- **Two design rules earned the hard way:** DR5 (a defect is never in one
  place - the sweep is part of the fix) and DR6 (an entry is closed in the
  change that fixes it).
- **A pile that shrank:** 31 open items to **9**.

## Known and accepted, versus open

**Known and accepted** - decided, with what would reopen each:

| | |
|---|---|
| The sandbox is advisory | path confinement, a Job Object, and a timeout. It stops a model that reaches; it does not stop code written to defeat it. Reopens on genuinely untrusted code, or a second user (D32) |
| File size is unlimited on Windows | no per-process equivalent; the container was the only route and Phase 6 is withdrawn |
| M11 is retired unanswered | reopens on a live suite, a closed D19, and both M7.4 rules wired (FINDINGS 1.27) |
| Phase 12 is withdrawn | its subject retired with M11 |
| Harness v3 is rejected | measured, iteration one |
| Phase 9.2's summarizer is withdrawn | there is no conversation history to summarize that is not tool output (C49) |

**Open: 9.** `docs/DEFERRED.md` states each entry's state in its own heading.

| item | one line |
|---|---|
| D2 | the pathological model call is detected and recovered, never diagnosed |
| D6 | the in-loop token estimator is uncalibrated. The instrument now exists; the number needs one run |
| D16 | per-backend context windows are unreachable over the OpenAI protocol |
| D24 | two tasks cannot approve identical plans. Fails loudly, unreachable in measurement paths |
| D26 | the Telegram and Anthropic live paths have never made a request. **The user has decided to supply both credentials** |
| D27 | the bot serialises tasks with no queue, because the chain is single-writer |
| D33 | tier 1 sits before the tool schemas; moving it needs a HARNESS bump |
| D38 | `max_taint_in` is dead in code and alive in prefix bytes, waiting for a bump - with a deadline |
| D39 | the CLI's 25 subcommands are reachable only through `--help` |

## What a third session picks up first

1. **D39** - a test per CLI subcommand, against a scratch database. Every layer
   of that defect so far has contained something; Part 3 found two defects in
   an hour of running five by hand.
2. **The calibration** - the positive control on `--split eval`, with the
   caveat that at n=15 the gate fires only on a large effect (FINDINGS 3.2).
3. **D6's number** - one ordinary run, then read `est_input_tokens` against
   the server's own count.
4. **D26**, the moment the credentials exist: one command each, in the README.

---

# ITERATION ONE: COMPLETE — 2026-09-05

Marked complete on the terms this document set out: *a measurement, plus an
instrument honest enough that the measurement can be checked.* Both exist. The
original research question does not have an answer, and section "What was not
answered" below says why in plain terms.

## Delivered

| | |
|---|---|
| **The runtime** | M1–M13, 376 tests, entirely offline. Plans, executes, verifies, refuses instructions embedded in fetched content, hash-chains its own trail |
| **The packaged wheel** | Builds and runs a task from another directory (`scripts/postinstall_check.py`) |
| **The ladder instrument** | `bench/ladder.py` — six rungs of escalating scaffolding. Now suite-agnostic: prompts are data, the grader path is explicit, an unknown task raises rather than falling back |
| **The reference measurement** | 30 tasks, `granite4.1:3b`, harness v2. **24.1% raw / 28.0% timing-clean.** Per-tier, per-task, with the machine-fault exclusions named. The first complete pass in the project's history |
| **Two findings** | Answer position discriminates correctness (FINDINGS 1.6); trap decomposition is a pre-test for skill measurability (1.14) |
| **The defect taxonomy** | Six instrument defects, five process defects, twelve deferred. Two classes with a stated pattern for each, and a fourth and fifth instance found in this iteration by applying that pattern deliberately |

## What was not answered

**The research question — do self-generated skills help? — was not answered.**
Not deferred, not partially answered: not answered.

**Why: the substrate could not support the measurement.** Phase 2B decomposed
every failing value from the reference run. Three of nineteen numeric failures
correspond to a trap the suite declares. The rest are unstructured — negative
escape velocities, 5.3e12, 5.2e-23, 0.0.

A skill repairs *structured* error. A units-discipline procedure can fix "read
km as m"; nothing written in a procedure fixes a program that ran and never
implemented the physics. So the paired trial could not have produced
discordant pairs regardless of how good the skill was, and the gate's floor of
10 was unreachable with 3 decomposable failures across 2 families.

This is a property of the **model–task pairing**, not evidence about skills.
It is also not a failure of the gate: the gate was never given a chance to
fire, which is precisely what the pre-test in FINDINGS 1.14 exists to detect
*before* four hours are spent.

Consequently:

- **M11, the skill generator, never ran.** By design — the calibration
  positive control never passed, and a gate that has not been shown to detect
  a real effect makes every later rejection uninterpretable. **Retired
  unanswered 2026-09-15** (FINDINGS 1.27).
- **No skill has been through the gate.** Not the calibration skill, not a
  generated one.
- **The 8B tier was not measured at suite scale.** It does not fit this
  hardware at the harness's context length: 7.64 GB resident against 3.6–6.4
  GB free. An evidenced correction to the spec's laptop-tier assumption
  (SPEC-DELTA C20), recorded rather than worked around.

## What the next iteration needs

Not a bigger suite — a **different** one, chosen from evidence rather than
intuition, and sized to what a 3B can actually do inside a harness. Two
measurements from this iteration constrain that choice:

1. **The failures must be structured** for any skill experiment to be
   possible. Run the decomposition pre-test on a candidate suite before
   committing to paired trials.
2. **The task shape matters more than the domain.** E01's goal is 115 words of
   which 10 are physics and 105 are I/O contract. The spec's own stated scope
   for a local tier is "summarizing, sorting, extracting" — which this suite
   never was.

The aerospace suite is archived, not deleted, and every number above remains
reproducible from `bench/suites/archive/`.

## Honest status of the checklist above

Items in section 1 referring to `tests/test_bench_suite.py` and the 30-task
aero suite now refer to **archived** components. They still pass, from
`tests/archive/`. The test count in that section (223) predates this
iteration; the current count is **376**.

---

# Goal-by-goal scorecard — audited 2026-09-06

The spec set eight numbered goals. Iteration one pursued the research question
(G4) and produced a reference measurement; this records where the other seven
actually stand, so a reader does not have to reconstruct it.

**Status vocabulary**

* **implemented and measured** — it exists, it runs, and a number exists for it
* **implemented and unmeasured** — it exists and is enforced at a named point,
  but no measurement of its *effect* was ever taken
* **partially implemented** — some of it exists, named parts do not
* **not implemented** — no working path

| Goal | Status |
|---|---|
| G1 ≥10× reduction in **paid** tokens per completed task | **closed 2026-09-25: not supported on granite4.1:3b** — per-mechanism savings measured, fire rates ~0; see below and FINDINGS 1.35 |
| G2 Mobile remote control | **partially implemented** — HTTP API + auth exist; nothing mobile |
| G3 Multi-agent as context isolation | **implemented and unmeasured** |
| G4 Research-grounded, verified skill generation | **the gate works; the generation half is RETIRED UNANSWERED** (2026-09-15, FINDINGS 1.27) |
| G5 Least-privilege security with provenance | **implemented and measured** |
| G6 Durable, resumable, interruptible execution | **implemented and measured** |
| G7 Self-measuring, eval harness first-class | **implemented and measured** |
| G8 Portable skill format | **implemented; the boundary crossing is demonstrated** (2026-09-16) |

---

## G1 — ≥10× reduction in paid tokens. The headline claim, and it is unverified.

> **CLOSED 2026-09-25 (FINDINGS 1.35).** G1 is now stated as two measured
> quantities per mechanism - the token saving when it fires, measured
> deterministically, and how often it fires on granite4.1:3b, read from the
> records:
>
> | mechanism | saves when it fires | fires on the 3B |
> |---|---|---|
> | compaction | ~24% uncached input | 0 of 254 steps (every boundary recorded was forced) |
> | semantic cache | 0 executor tokens; 2 of 3 local compressions | 0 of 253 steps |
> | prefix stability | one prefix re-prefill per mid-task memory write | not measurable: 0 attempts, and isolation refuses tier-1 writes |
> | code-as-action | ~77% on a four-file read, 5 calls -> 2 | 92 of 199 steps, all on one task type |
>
> **The ≥10x claim is not supported on this executor.** The one mechanism
> effect measured is in PASS RATE and against the mechanism: without
> code-as-action the 3B passed 2/5 in each of 3 runs, with it 0/5 in each of
> 3 runs (n=3 runs x 5 tasks, temperature 0, the same two tasks each time).
> The executor property that would change G1 is a model that reads before
> answering. Everything below is the history that led here; its numbers are
> void where FINDINGS 1.30, 1.33 and 1.34 say so.

**The decisive number.** Across the whole project history the telemetry table
holds **1,179 recorded real model calls** with the figures below. *Recorded*:
until 2026-09-15 a call that never returned, and every deputy review, wrote
no row. The record holds more calls than `calls` does (CHANGELOG 2026-09-15,
C59). They are:

```
SUM(cost_usd)          = 0.0
SUM(cache_read_tokens) = 0
backends used          = {"cloud"}   ← which config.local.yaml maps to a LOCAL model
```

**Paid tokens to date: zero.** G1 is a ratio of paid tokens against a
baseline, and both terms are zero. It is not "weakly supported" — it has never
been evaluated at all.

### The six mechanisms: implemented, enforced, unmeasured

| Mechanism | Enforcement point | Enforced? | Effect measured? |
|---|---|---|---|
| Prefix stability | `PrefixGuard.check()`, constructed at `execute/loop.py:200`, raises `PrefixMutationError` on drift | **yes**, raises | **now measurable, not yet measured at suite scale** — corrected 2026-09-06. This cell previously read "unmeasurable on Ollama, which has no cache accounting". That was WRONG: the accounting exists and the native protocol discards it. Through `/v1`, the same server on the same weights reported `cached_tokens` **5 then 205** across two identical calls on a 206-token prompt it had provably never seen, against **0 and 0** on `/api/chat`. `docs/evidence/phase1-openai-parity.json`, SPEC-DELTA C28 |
| Deletion-based compaction | `compact()` at the loop's session boundary and in `context.handle_overflow_by_delegation` | **yes**, and **ablatable** since 2026-09-17 (`mechanisms.compaction`) | **no** — no before/after token count is recorded per compaction, but a control arm can now be run |
| Semantic cache | `cached_op()` at `execute/loop.py:594` | **yes** | **no** — hit rate is recorded but no token saving is attributed |
| Code-as-action | inline in `execute/loop.py`; `run_code` batches tool calls into one program | **yes** | **no** — never compared against a one-tool-call-per-turn arm |
| Context ceiling | `enforce_ceiling()` at `execute/loop.py:242` | **yes** | **no** — and it budgets against an **uncalibrated** estimator (DEFERRED D6) |
| Routing / cascade | `ctx.router.choose()` at `execute/loop.py:254` and `:565` | **yes**, but **structurally inert**: one tier is configured, so `choose()` returns the same backend every call | **no** — `escalation_rate: 0.0` over 1,179 calls |

**Has any run ever compared paid tokens per task against a baseline?**

**No. Plainly: no such run exists, and no such run could have existed.** Every
call in the database cost $0.0000.

### What it would take to measure G1 honestly

Three things, and the third is the one that is usually forgotten.

1. **The Anthropic adapter must make its first live call.** It is written and
   unit-tested and has **never** been exercised against the API
   (FINDINGS 1.25). That needs an `ANTHROPIC_API_KEY` — which
   `--profile local` refuses to start with, by design, so this is necessarily
   a different profile.

2. **~~Token counts must be calibrated.~~ Corrected 2026-09-15.** This said
   every token figure in this project is an estimate from a local heuristic.
   It is not: recorded usage is the model server's own count (Ollama's
   `prompt_eval_count` / `eval_count`, the OpenAI protocol's `prompt_tokens`
   / `completion_tokens`, Anthropic's `usage`), so G1's paid-token figures
   would be the provider's own and no calibration stands in their way. What
   is uncalibrated is the in-loop estimator the context ceiling budgets
   against (DEFERRED D6). `tokens.calibrate`, which would have measured it
   against Anthropic's counter, is deleted: the wrong tokenizer for the local
   models, and it needed a key.

3. **~~A control arm must exist, and today it does not.~~ BUILT 2026-09-17.**
   This was the blocker nobody had costed. "10× reduction" is a comparison
   against the same work done *without* the mechanisms, and there were **no
   on/off switches** for compaction, the semantic cache, prefix stability or
   code-as-action: they were structural in `execute/loop.py`, and only
   `context_fill_ratio` and `usable_context_window` were tunable, which move
   the ceiling rather than removing it.

   `config.MechanismsConfig` now carries one flag each, defaulting to the
   shipped system, and the settings travel in `profile_provenance` so no run
   record can be mistaken for the other arm. Each flag removes the
   **mechanism**, never merely its enforcement — the distinction that decides
   whether an arm measures anything:

   * *compaction* off stops both `compact()` call sites, the loop's session
     boundary and the refused-delegation fallback, and records
     `compaction.skipped` on the chain where the ceiling was crossed. One
     alone would have left the arm compacting while its record said otherwise
     (DR3).
   * *semantic cache* off neither reads nor writes. A read-only bypass would
     still have warmed the store for whichever arm ran next.
   * *prefix stability* off re-reads skills and tier-1 memory LIVE each step
     instead of snapshotting them at task start. Disarming `PrefixGuard`
     alone would have changed no prompt byte: the guard DETECTS stability,
     the snapshots CREATE it (DR2).
   * *code-as-action* off removes `run_code` from the registry, from the tool
     schemas, from the tool manual, from the sandbox-library note that
     described what may be imported inside it, and from the system contract.
     A model still told to emit `<code>` and then refused would measure a
     model arguing with a denial, which is adjacent to the question and not
     it.

   **Run 2026-09-17, and the result is mostly about the suite.** Five arms,
   37 minutes, every one scoring 0.75. Three of the four ablations were
   **re-runs of the control**: the compaction ceiling sits at 4,587 tokens and
   the largest call in the run was 2,320; the semantic cache is switched off by
   `compress_observations_over_tokens: 100000` before `cached_op` is reached;
   and the prefix-stability arm cannot differ while nothing writes memory
   mid-task. Only code-as-action produced a contrast - `run_code` receipts 9-10
   → 0, +4.8% input tokens, +115 s of model wall clock, same pass rate.

   The design question the estimate flagged is **answered for this suite**: the
   model DID complete the same tasks without `run_code`, so the arm measures
   the mechanism rather than the suite's dependence on it. At n=4 that is a
   sighting.

   ~~**What G1 now needs is a suite, not more switches:**~~ *(Superseded 2026-09-25: four suites later it needed an executor that reads. FINDINGS 1.35.)* tasks whose
   observations cross a 4,587-token ceiling and repeat enough to hit a cache.
   FINDINGS 1.30, `docs/evidence/g1-ablation-2026-09-17.json`.

**Estimated scope, stated because prior estimates here ran optimistic:** an
API key and one live call is an afternoon. Calibration is another. The
ablation switches plus a paired paid/unpaid comparison across a task suite is
the larger part, and it is the part that decides whether the number means
anything.

---

## G2 — Mobile remote control. Partially implemented.

**Exists:** a 32-endpoint HTTP API, bearer-token auth, CSRF and DNS-rebinding
protection, and an `ALPHA_ALLOW_REMOTE` env gate in `control/guard.py` that
deliberately refuses off-loopback binding unless set. A 569-line operator
console is served at `GET /`.

**Does not exist:** anything mobile. No responsive layout, no mobile client,
no tested remote deployment. The console was built for a desktop browser.

**Reason there is no measurement:** the remote path has never been exercised
off-loopback. Everything verified in this project bound to 127.0.0.1.

The honest reading: **the API that mobile control would need is real and
tested; the mobile control itself was never started.**

---

## G3 — Multi-agent as context isolation. Implemented and unmeasured.

`delegate/contracts.py` implements the five-gate test, and it **is** wired:
`execute/loop.py:250` dispatches a `delegate` action and `context.py:287`
calls `may_delegate`.

**Unmeasured because it almost never fires.** The design intent is that most
delegations are refused — the gates are strict — and no run in this project
produced a delegation that passed them. So the mechanism is live, and its
effect on context size has never been observed.

Not to be confused with routing (G1's cascade), which is separately inert for
a different reason: one configured tier.

---

## G4 — Research-grounded, verified skill generation. Partially implemented.

This is the research question, and `RELEASE.md` above states the outcome. In
goal terms:

* **The verification half is real and measured.** The gate (`skills/gate.py`)
  implements paired trials, McNemar, and the promotion rule; 
  `bench/coverage.py` bounds discordant pairs; the trap decomposer
  (now `bench/archive/decompose_aero.py`) classifies failures.
* **The generation half never ran, and is retired unanswered (2026-09-15).**
  `generate_skill`, its grounding (`render_grounding`) and the M7.4 rules
  that policed it are deleted. FINDINGS 1.27 records why - a -1.3 pp
  published baseline, an archived suite, D19 unclosed, three components never
  run - and what would reopen it.

**Reason there is no measurement:** deliberate ordering — the gate was built
first — and then Phase 2B established that the substrate could not support the
measurement at all (FINDINGS 1.13). Two of the gate's own guards were
additionally inert, and both are gone as of 2026-09-15:
- `record_holdout_gap` and the anti-gaming veto were retired with M11. A
  promotion now states why the veto does not apply: every check is held out
  and the grader is unreachable (C1).
- `measure_retrieval_precision` was withdrawn: no skill has ever been
  promoted, so it measured 0.0 by construction.

**Scope statement, 2026-09-15.** M7.4 rules 1-3 policed generated skills and
were retired with the generator, never having run where it mattered (FINDINGS
1.21, 1.27). Rule 4, trusted-tier actions never auto-reviewed, stands.

---

## G5 — Least-privilege security with provenance. Implemented and measured.

The one goal with an end-to-end demonstration on every run of `demo.py`:

```
gate refused append_to_outbox: append_to_outbox requires <=user arguments,
args are untrusted
message actually sent to the address from the page: no
```

A three-level taint lattice (`security/taint.py`), a capability gate that
denies on argument taint (`security/capabilities.py`), and a sandbox that
confines writes to `artifact_scope` — the last re-verified this iteration with
sympy present, where an out-of-scope write was still refused.

**Caveat kept from the source:** the sandbox is honest about being a guard
against mistakes, not against an adversary with code execution.

**The sandbox is a door, not a wall - 2026-09-14.** It now refuses, for agent
code, out-of-scope writes and reads, every file and database route into the
project's own state that has been tested, process starts and native
extensions. It stops a model that reaches. It does not stop code written to
defeat it - the hook runs inside the process it polices (D32). A container per
run was the only thing that would make it a wall, and **Phase 6 is withdrawn -
2026-09-16, by decision, not deferral** (FINDINGS 1.29). D37's Job Object
carries most of the isolation benefit; what remains uncovered is file size,
which on an attended single-user machine is resource exhaustion rather than a
boundary. **The sandbox is advisory path confinement, a Job Object, and a
timeout.** D32 is now KNOWN AND ACCEPTED, and names what would reopen it:
genuinely untrusted code, or a second user.

**Resource limits - 2026-09-15.** The day began with the Windows sandbox as
advisory path confinement plus a wall-clock timeout: no OS-enforced limit at
all, on the only platform this project has run on. It ended with a Job Object
(D37). On Windows the kernel now enforces memory (512 MB per process), CPU
time (the task's timeout) and process count (32), and each limit was tested
for real on this machine. File size is still unlimited on Windows and needs
the container. The POSIX path sets all four with `rlimit` and has never run.

---

## G6 — Durable, resumable, interruptible execution. Implemented and measured.

Measured **in anger**, not just in tests. Phase 2A was killed at task 5 of 30
by a session teardown and **resumed from the fsynced ledger**, replaying 4
completed tasks and continuing — the run that produced the reference
measurement would not exist otherwise.

Supporting: a hash-chained event log that refuses to resume into a tampered
state, per-task fsync in the ledger, an OS-level run lock, and `kill_tree` for
process-tree termination.

**The known gap, already logged:** user-facing stop paths do not route through
`kill_tree` (DEFERRED D10), so a run believed stopped may still be running.
That is an *interruptibility* defect against a goal otherwise met.

---

## G7 — Self-measuring, eval harness first-class. Implemented and measured.

The strongest-supported goal after G5, and arguably the iteration's real
output. Six harness tools (`noise`, `run`, `ladder`, `coverage`, `report`,
`nextstep`), 376 tests, and a documented defect taxonomy of six
instrument-class and five process-class defects — several of which were found
*by the harness measuring itself*.

Concrete evidence it is first-class rather than decorative: `report.py`
refuses to plot across harness or toolset versions; the run record carries
per-task memory, machine-fault and wall-outlier flags; and
`test_dead_code_audit.py` fails when a component is implemented but never
called.

---

## G8 — Portable skill format. Implemented; one half demonstrated.

Skills are plain markdown with at most three modules
(`examples/skills/aero_units_discipline.md`), stored content-addressed by hash
in `skills_store/`. Two skills exist.

**Unmeasured** in the sense that portability has never been exercised:
`import_external_skill` is in `KNOWN_INERT` ("External skills go through the
same gate; none imported yet"), and no skill has ever moved between machines,
suites, or models. The format is a claim, not a demonstrated property.

---

## Code that has never run for real - 2026-09-14

FINDINGS 1.25 has the inventory. In brief: the measurement record exercised
the loop, the gate's allow path, receipts, the deputy, verification and the
measurement apparatus, and nothing else. **Sixteen paths** have run only
against a mock or a fake - among them both credential-gated adapters, every
network tool, memory, sessions, compaction, delegation, cron, and the release
of a human decision. **Nine components cannot run under any current
configuration.** No production task has ever been resumed, and the
capability gate has never refused a production call.

---

## What is UNMEASURABLE on this hardware, and why

| Goal / component | Why |
|---|---|
| ~~**G1 prefix-cache benefit**~~ | **MOVED OUT OF THIS LIST, 2026-09-06.** The reason given here — "Ollama has no prompt-cache accounting" — was false. Ollama has it; its *native* protocol does not report it. The OpenAI-compatible endpoint on the same server does, so the ratio is measurable on the local tier under `config.openai.yaml`. The historical statement stands for the runs it describes: `cache_read_tokens` is 0 across all 1,179 real calls, and those runs are recorded with `cache_hit_ratio: null` — **unmeasured, which is not zero** — rather than being back-filled. Still unmeasured *at suite scale*: no pass has yet run through the reporting adapter |
| **G1 paid-token reduction** | No paid backend has ever been called. Requires an API key and a live Anthropic call. ~~and ablation switches that do not exist~~ — **the switches were built 2026-09-17**; see G1's point 3. *(Calibration was listed here too; removed 2026-09-15 - the provider reports paid tokens itself, C62)* |
| **G1 routing / escalation** | One configured tier. The 8B alone is 7.64 GB resident against 3.6–6.4 GB free, so two models cannot be co-resident; a "routed" run would measure model swapping |
| **8B at suite scale** | Does not fit. 7.64 GB resident at the harness's `num_ctx: 16384`. Measured, and recorded as SPEC-DELTA C20 |
| **Token counts generally** | *Corrected 2026-09-15:* this said every token figure is an estimate. Recorded usage is the model server's own count. What is uncalibrated is the in-loop estimator invariant 11's context ceiling budgets against (DEFERRED D6); `calibrate` is deleted (C62) |
| **G4 anti-gaming veto** | Never fired - nothing recorded the gap it read. Retired with M11, 2026-09-15 (FINDINGS 1.27). Every promotion states why it does not apply: every check is held out, the grader is unreachable (C1) |
| **Cost axis in dollars** | Every call is $0.0000 under `--profile local`. The Pareto's cost axis is wall-clock seconds, and the dollar column is retained as `n/a` |

---

## The honest one-line summary

**Three of eight goals are measured (G5, G6, G7). One is partially built with
its measured half working and its generative half never run (G4). Three are
implemented and enforced but their effect has never been observed (G1, G3,
G8). One barely started (G2).**

The headline claim — G1's 10× — is the least supported of the eight, and the
gap is not a missing run. It is a missing API key, a missing calibration, and
a missing control arm, in that order of increasing cost.
