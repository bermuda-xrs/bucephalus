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
- [ ] **No LICENSE.** Deliberate — that is the author's decision, and an
      unlicensed repository is "all rights reserved" by default, which may
      or may not be what is wanted
- [x] `CHANGELOG.md` reflects what actually changed, including what is still
      undone

## 5. Before tagging

- [ ] `git log --oneline` reads as the project's memory — no commit message
      claims more than was verified
- [ ] `docs/HANDOVER.md` §11 (what has never been measured) matches reality
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

- **M11, the skill generator, has never run.** By design — the calibration
  positive control never passed, and a gate that has not been shown to detect
  a real effect makes every later rejection uninterpretable.
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
| G1 ≥10× reduction in **paid** tokens per completed task | **implemented and unmeasured** — and currently unmeasurable, see below |
| G2 Mobile remote control | **partially implemented** — HTTP API + auth exist; nothing mobile |
| G3 Multi-agent as context isolation | **implemented and unmeasured** |
| G4 Research-grounded, verified skill generation | **partially implemented** — the gate works, the generator has never run |
| G5 Least-privilege security with provenance | **implemented and measured** |
| G6 Durable, resumable, interruptible execution | **implemented and measured** |
| G7 Self-measuring, eval harness first-class | **implemented and measured** |
| G8 Portable skill format | **implemented and unmeasured** |

---

## G1 — ≥10× reduction in paid tokens. The headline claim, and it is unverified.

**The decisive number.** Across the whole project history the telemetry table
holds **1,179 real model calls** with:

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
| Deletion-based compaction | `compact()` at `execute/loop.py:249` and `context.py:290` | **yes** | **no** — no before/after token count is recorded per compaction |
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
   (`HANDOVER` §11). That needs an `ANTHROPIC_API_KEY` — which
   `--profile local` refuses to start with, by design, so this is necessarily
   a different profile.

2. **Token counts must be calibrated.** `tokens.calibrate` has **no caller**
   (confirmed in `KNOWN_INERT`), so every token figure in this project is an
   estimate from a local heuristic. A 10× claim built on an uncalibrated
   estimator is not a measurement. Calibration also *cannot* run locally —
   `count_tokens_exact` needs the provider client.

3. **A control arm must exist, and today it does not.** This is the blocker
   nobody has costed. "10× reduction" is a comparison against the same work
   done *without* the mechanisms. Checking `config.local.yaml` and
   `config.py`: there are **no on/off switches** for compaction, the semantic
   cache, prefix stability, or code-as-action. They are structural in
   `execute/loop.py`. Only `context_fill_ratio` and `usable_context_window`
   are tunable, and those move the ceiling rather than removing it.

   So even with a funded API key and a calibrated tokenizer, **there is
   nothing to compare against.** Measuring G1 requires first building an
   ablation path for each mechanism — which is the same leave-one-in design
   problem the skill ablation hit, and it is a design task, not a run.

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
* **The generation half has never run.** `generate_skill` is in `KNOWN_INERT`
  — "M11. No generated skill has been produced yet - by design, the gate was
  built first."
* **Grounding with provenance** is implemented (`render_grounding`) and
  likewise unexercised.

**Reason there is no measurement:** deliberate ordering — the gate was built
first — and then Phase 2B established that the substrate could not support the
measurement at all (FINDINGS 1.13). Two of the gate's own guards are
additionally inert: `record_holdout_gap` (so M7.4's anti-gaming veto cannot
fire) and `measure_retrieval_precision`.

**Scope statement, 2026-09-14.** M7.4 is one rule of four enforced where it
matters: rules 2 (the visible/held-out gap) and 3 (isomorphic verification)
have no production caller, so no verification in this project has been
cross-checked by a second verifier, and no promotion has carried a measured
gap. They were not wired: each is a design decision, not a line (FINDINGS
1.21, D22). The gate they form holds in code - `generate_skill` refuses
(`GenerationGated`) until they run.

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

## G8 — Portable skill format. Implemented and unmeasured.

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
| **G1 paid-token reduction** | No paid backend has ever been called. Requires an API key, a live Anthropic call, calibration, and ablation switches that do not exist |
| **G1 routing / escalation** | One configured tier. The 8B alone is 7.64 GB resident against 3.6–6.4 GB free, so two models cannot be co-resident; a "routed" run would measure model swapping |
| **8B at suite scale** | Does not fit. 7.64 GB resident at the harness's `num_ctx: 16384`. Measured, and recorded as SPEC-DELTA C20 |
| **Token counts generally** | `calibrate` has no caller and cannot run locally. Every token figure is an estimate — including the one invariant 11's context ceiling budgets against (DEFERRED D6) |
| **G4 anti-gaming veto** | `record_holdout_gap` has no caller and a `verify_program` suite has no visible/held-out partition. Every promotion carries `gaming_check: NOT MEASURED` |
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
