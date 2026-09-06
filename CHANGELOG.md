# Changelog

> **The aerospace suite is archived.** As of 2026-09-05 `aero_calc` lives
> under `bench/suites/archive/`, `bench/tasks/archive/` and
> `bench/archive/decompose_aero.py`, and is no longer any tool's default —
> `--suite` and `--tasks` are now required arguments. Every result below
> remains reproducible from the archive. Nothing was deleted. See FINDINGS
> 1.13 for why it was retired.

Dates are commit dates. `0.1.0` is not released yet — see "Unreleased" for
what still stands between here and a first iteration.

## Iteration two, phase 1 — one adapter for the OpenAI protocol — 2026-09-06

**Invalidates no published number.** `HARNESS_VERSION` stays 2 and
`TOOLSET_VERSION` stays 1: no tool or library was added, and nothing here
changes what counts as a pass. Runs through `config.openai.yaml` are a
different instrument from native-adapter runs and must not be pooled with
them — the adapter `kind` already travels into every run header through
`profile_provenance["backends"]`, so the record distinguishes them.

### Added

* **`models/openai_compat.py`** — one adapter speaking OpenAI
  chat-completions to Ollama's `/v1`, vLLM, llama.cpp's server or a hosted
  endpoint. Written on httpx rather than the `openai` package because the
  stall detector needs the raw stream: a client that returns a completed
  object has already spent the wall clock this project budgets in.
* **`models/stall.py`** — the machine-fault rule, lifted out of the Ollama
  adapter unchanged so both adapters share one implementation. A stall is a
  property of the machine, not of an adapter. The names stay re-exported from
  `models/ollama.py` because `bench/noise.py` and `execute/loop.py` import
  them from there, and a machine-fault path is the wrong place to churn
  imports for tidiness.
* **`config.openai.yaml`** — `config.local.yaml` with the adapter swapped and
  nothing else, pinned by a test that compares the two files field by field.

### Measured — the two APIs are not the same instrument

Ollama 0.33.3, granite4.1:3b, same server, shipped adapters. Full record in
`docs/evidence/phase1-openai-parity.json`; SPEC-DELTA C28 and C29.

* **Identical** where a benchmark scores: answer text, `prompt_tokens` (32),
  `completion_tokens` (12), and JSON-schema constrained decoding — including
  the real `ACTION_SCHEMA`.
* **`/v1` reports MORE.** It carries prompt-cache accounting the native API
  does not. On a 206-token prompt built round a fresh uuid: **5 cached tokens
  on first sight, 205 on the identical repeat**, against 0/0 native. Near-zero
  then near-total is the shape of real accounting, not a constant. The audit
  in `RELEASE.md` recorded the stable-prefix benefit on the local tier as
  **unmeasurable for want of cache accounting** — it is measurable, and was
  being discarded by the protocol rather than withheld by the server. The
  first real task through the new adapter printed **cache hit 41%** on the
  CLI cost line, where native runs print 0.
* **`/v1` honours LESS, twice.** `keep_alive` is accepted and silently
  discarded — HTTP 200, model still expires in 5.0 minutes. And there is no
  `num_ctx`, so `usable_context_window` is a claim about the server rather
  than a request: a real task logged `n_ctx_slot = 4096` against a configured
  16384. Both are now set on the server by `ollama-serve.ps1` / `.cmd`
  (`OLLAMA_KEEP_ALIVE=30m`, `OLLAMA_CONTEXT_LENGTH=16384`, verified in the
  log). The context ceiling costs memory: **2.51 GB resident at ctx 4096
  against 3.61 GB at 16384**, server-wide, so every loaded model pays it.

**No speed comparison is reported.** The one back-to-back pair read 53.5s on
`/v1` against 12.4s native, but the native call ran second against a cache the
first had just filled and the replies differed in length. Two confounds, n=1.

### Changed — two class-name checks became declared properties

`cfg.kind == "ollama"` decided whether the model is handed a constrained
action envelope, in `execute/loop.py` and again in `plan/review.py`. With a
second adapter able to reach the same server running the same weights, that
test flips while nothing about the model changes — the run would ask a
different question and not say so. Both now read
`BackendConfig.needs_action_envelope`. Shown rather than asserted: the old and
new predicates agree on **all 7 backends across both shipped configs**.

`profiles.enforce` decided locality the same way. It now derives it from where
a backend POINTS: cloud-only kinds, declared cloud tier, or a host that is not
loopback/private — and an unresolvable host counts as remote, because unknown
fails closed. `kind: openai, base_url: https://api.openai.com/v1,
trust_tier: local` is one careless copy of a working entry, and it would have
started a `--profile local` run and produced a number labelled local. Any
`api_key_env` a backend names is now watched alongside the fixed credential
list. The change only ever ADDS to the cloud set; nothing can clear a backend
the declared tier already flagged.

### Tests

**376 → 405.** The two structural checks the refactor invalidated were
replaced by stronger ones rather than deleted:

* the transport-timeout guard read `ollama.py` for the phrase
  `raise ModelStall`. It now INJECTS a real `httpx.ReadTimeout` into the
  request path and requires `ModelStall` out of `complete()`, parametrised
  over **both** adapters. The old check tested spelling and could say nothing
  about a second adapter.
* the injection switch was pinned as "read once in `ollama.py`". It is now
  "read once in the whole package, and that once is `stall.py`" — the narrow
  version would have passed while a new adapter read the switch for itself.
* added: the class `bench/noise.py` catches is the same object both adapters
  raise, asked as an identity question rather than inferred from imports.
* added: the stall RE-RUN, exercised instead of grepped for. The existing
  check read `noise.py` for the word `continue` in the stall branch, which
  passes for any code containing the token and says nothing about whether a
  second attempt happens. `run_once` is now stubbed to stall once and then
  succeed, and the recovered row is inspected: one re-run, `machine_fault`
  recorded, `excluded` false. Phase 1's acceptance asked whether the
  machine-fault path works on the new adapter, and answering that by
  reasoning about which class gets caught is the inference this project keeps
  getting wrong.

### Found

* **A fourth machine stall**, unplanned, during the first end-to-end run:
  `init_sampler` at 1165 tokens, then no eval timing at all, then cancel;
  `500 | 17m57s`. Not reproducible from the request shape — `ACTION_SCHEMA`
  alone, streaming with tools and schema, and the same shape at the real
  prompt size all returned in 5-54s. Same signature as the three already
  documented. **The machine-fault path fired on the new adapter, on a real
  event**, converting the timeout to `ModelStall` rather than to a task
  failure. n=1 says nothing about whether `/v1` stalls more often, and no such
  claim is made.
* 19 pre-existing anchor mismatches in the gitignored local dev database,
  none of them from today's runs, consistent with D10 — a manually stopped
  run never checkpoints. Logged, not chased; the freeze holds.

## Phase 2 (B4) — FTS5 search over prior sessions — 2026-09-06

`search_history`, an FTS5 index over what earlier runs were asked and what
they produced. `alpha search <query>`, `--rebuild`, `--verify`.

**Results are UNTRUSTED as a floor, not as a computation.** `answer`
artifacts are stored `taint=trusted` and propagating that would have been the
hole: an answer is trusted as the output of a verified run, not as input to a
later one, and its text may quote a page the agent fetched three steps
earlier. Search re-injects old content into a fresh context, which is a read,
not a memory. A computed join would have returned TRUSTED for exactly the rows
most worth attacking. The taint reaches the capability gate by the single
shared site in `execute/loop.py` that every tool uses — pinned by a test, so a
tool cannot be special-cased out of the gate.

**What is indexed:** task contracts, and `answer`/`plan`/`doc`/`skill_draft`
artifacts. **Not `tool_output`**, which is 348 of 460 artifact rows in the
live database — directory listings and program output that would bury the two
sentences a person actually wrote.

**Maintained by SQL triggers, not by call sites.** Two Python functions write
artifacts today; a third would silently stop indexing, and a derived table
that is quietly incomplete is worse than none — a search that finds nothing
looks exactly like a session that never happened.

**The index is derived data and the log wins.** Verified rather than asserted,
on a copy of the real database: 7700 events before a full rebuild, 7700 after,
zero heads changed, zero chain breaks. `--verify` reports disagreement without
repairing it, because a count mismatch means a write path bypassed the
triggers — a defect to find, not a number to correct.

**Bounded.** Default 5 results, hard cap 20, snippets capped at 400 chars,
with an offset for paging. A tool that can paste fifty matches into context
defeats the ledger discipline that makes the rest of this system affordable.

**On "session":** there is no sessions table until Phase 9, so a session is
the root of the task tree — a delegated child reports its parent's session.
That is the column Phase 9 repoints; callers do not change.

Real-data check: the index was empty on the existing database (the schema
arrives on open, the rows do not), rebuilt to 395 rows from 283 contracts and
112 artifacts, and found E03's escape-velocity task from a prior session.

Found while verifying the chain: **D17**, 19 tasks reporting "head does not
match anchor" at `severity: critical` when the chain is intact and the anchor
is merely behind. Pre-existing, unrelated, logged not fixed.

`HARNESS_VERSION` unchanged. `TOOLSET_VERSION` **bumped to 2** — a new tool
changes what a run can achieve, and `report.py` already refuses to plot across
it.

## Corrections to phase 1 — 2026-09-06

Three corrections and two new recording requirements, all arising from what
phase 1 measured rather than from new scope.

### The prefix-cache alarm can now fire, and had two independent reasons not to

`Telemetry.cache_alarm` is a monitored invariant from M3.2 that has never
fired once. Both reasons are now fixed and either alone would have left it
dead:

* No local backend reported cache accounting, so the ratio was a constant
  0.0. Phase 1 measured that the SAME SERVER reports it over `/v1`.
* It was gated on `paid_tokens > 0`, which is never true under
  `--profile local`. The one machine this project runs on was the one machine
  the alarm could not protect. The gate encoded the spec's COST axis; on this
  hardware a prefix mutation is paid in wall clock. SPEC-DELTA C30.

**`cache_hit_ratio` is now `float | None`.** `None` means unmeasured and is
not the same as 0.0, which means a reporting backend saw nothing cached — the
actual alarm condition. **Every run recorded before 2026-09-06 reads as null,
not zero, and is NOT back-filled.** A chart must not treat them alike.
SPEC-DELTA C31, invariant 36.

Added: `cache_hit_ratio` per task and `cache_hit_ratio_median` per run in
`bench/noise.py`, plus a `cache_drop` flag when a task falls below half the
run's running median of measured tasks. Same shape as the wall-clock outlier
detector, and a SIGNAL rather than a controller — the commit-ratio episode is
the precedent for why.

**No published number moves.** The reference measurement used no cache
accounting and none is invented for it.

### The class-name defect swept rather than found one at a time

Three instances had been found individually — `profiles.enforce`,
`execute/loop.py` (action schema), `plan/review.py` (deputy schema). The last
two decide **what the model is asked**, which is worse than the first.

An AST sweep of `src/` and `bench/` found no fourth instance. Two sites branch
on adapter kind legitimately and both are recorded with reasoning:
`models/registry.py` (it IS the registry) and `profiles.py`
(`CLOUD_ONLY_KINDS`, a kind→capability table OR'd with an endpoint check that
can only add to the cloud set). Three apparent hits are false positives —
`parsed.kind == "final"` and the injection spec's `kind == "stall"` compare a
differently-named `kind` against strings that are not adapter names.

`tests/test_no_adapter_identity_branches.py` fails if a new one appears, and
was verified by injecting one. Invariant 35.

### The n_ctx hazard did not reach any published number

Verified rather than assumed, and the retrospective half is honestly bounded:

* `options.num_ctx` entered the native adapter in `8a68f4c`, 2026-08-19. The
  earliest published evidence is 2026-08-26. `git log -S` finds exactly one
  commit touching that string, so it was never removed. Every published run's
  requests carried it.
* The mechanism was verified live on a STOCK server
  (`OLLAMA_CONTEXT_LENGTH:0`, default 4096): a native request carrying
  `num_ctx: 16384` produced `n_ctx_slot = 16384`.
* **What cannot be verified retrospectively:** `runs/ollama.log` is the GIN
  access log only — no slot lines — and no run record captured the Ollama
  version. So the mechanism is proven on 0.33.3 today, not read off the actual
  runs. Nothing suggests the version then behaved differently, and it cannot
  be checked. SPEC-DELTA C29.

### Cold-load and context state are now recorded (4A, 4B)

`bench/noise.py` preflight records whether the model was warm at start, the
configured `keep_alive`, the configured context window, and — from
`/api/ps` — what the server ACTUALLY allocated, warning loudly when the server
window is below the configured one. A wall-clock cost axis cannot silently
include a cold load in one arm and not the other, and the context window costs
2.51 GB at 4096 against 3.61 GB at 16384 on a 15.5 GB machine.

This also makes C29 catchable before a run rather than after: the check is not
portable inside the adapter, but the bench preflight already knows it is
talking to Ollama.

### A crash phase 1 introduced, fixed

`refresh_worker` called `b.endpoint.rstrip("/")`. On an `openai`-kind backend
`endpoint` is `None`, so any run under `config.openai.yaml` would have died at
the first worker refresh. Replaced with `_ollama_admin_base`, which handles
either protocol and strips a `/v1` suffix.

### Versions

`HARNESS_VERSION` and `TOOLSET_VERSION` both unchanged. Nothing here adds a
tool or library, and nothing changes what counts as a pass.

## Iteration one complete — 2026-09-05

The reference measurement exists, the research question does not have an
answer, and the reason is itself a result. See `RELEASE.md`.

### Measured

* **The reference run.** 30 tasks, `granite4.1:3b`, harness v2, randomised
  order, 3.32 h. **24.1% raw / 28.0% timing-clean** — two denominators that
  straddle the 25% band floor, differing only by four `budget_exhausted`
  tasks whose timing the instrument itself flagged. Easy 3/10, medium 3/11,
  hard 1/8. `done_unannounced` **zero**, against it being the 8B's dominant
  failure mode: the 3B terminates properly and its failures are wrong
  answers, not unannounced right ones.
* **Trap distribution.** 3 of 19 numeric failures decompose to a declared
  trap. The rest are unstructured — negative escape velocities on H28, H23
  and M17, plus 5.3e12, 5.2e-23 and 0.0.

### Concluded

* **No skill was measurable on this substrate**, and that is a finding rather
  than an absence. A skill repairs structured error; nothing in a written
  procedure fixes a program that never implemented the physics. Property of
  the model–task pairing, not evidence about skills.
* **Trap decomposition is a cheap pre-test for whether a skill experiment is
  possible at all** — minutes against a 4-hour experiment avoided. The
  `coverage.py` contrast sharpens it: coverage predicted 20/30, the
  measurement found 3, because declared-trap coverage is not measured-trap
  incidence.
* **Phase 3 was skipped** on that evidence: 3 decomposable failures across 2
  families against a gate floor of 10 discordant pairs. No route.

### Changed

* **The aerospace suite is archived**, not deleted. `git mv` throughout, 154
  files moved as renames. Every published number remains reproducible from
  `bench/suites/archive/`. It is no longer any tool's default — `--suite` and
  `--tasks` are required arguments and a missing suite fails loudly.
* **`ladder.py` is decoupled** rather than archived, because it carries the
  project's best finding. Prompts are suite-supplied data extracted
  programmatically rather than retyped; `expected_for()` takes a grader path
  instead of resolving against the process CWD; an unknown task raises.
  Acceptance shown rather than asserted: **16 prompt entries compared, 0
  differences; 43 verdicts compared, 0 changes.**
* **A sandbox-library layer**, separate from the tool surface. Libraries
  declare a pinned version and whether they touch network, write outside
  scope, or spawn subprocesses; registration refuses anything that would
  widen the sandbox. `TOOLSET_VERSION` is stamped on every run and
  `report.py` refuses to plot across differing values.
* **sympy 1.13.3** as the first entry, verified inside the real sandbox with
  three runs rather than assumed: `solve` → `[-sqrt(2), sqrt(2)]`, `simplify`
  → `x + 1`, and `900 km/h` → `250 m/s` through `sympy.physics.units`. It
  landed **after** the 2A measurement and is reflected in no published
  number — that run was TOOLSET_VERSION 0.
* **The abort rule now counts EXCLUDED only.** `machine_fault` is health
  telemetry with no abort authority. The previous rule conflated H23, which
  stalled once and produced a valid measured result, with M11, whose data was
  lost — and would have aborted a healthy run on evidence recovery worked.

### Found

Four more decomposer defects, all fixed during 2B and all **adversarial to
the conclusion**: every one moved the trap count **upward, 1 → 3**, pushing
against the STOP, and the STOP held anyway. Recorded in FINDINGS 1.15 as a
researcher-degrees-of-freedom disclosure rather than left to be noticed.

The residual gate sat **below the rounding floor of its own inputs, twice** —
a check that could not have matched anything, announcing nothing. It is now
per-task, three half-ulps of the last significant figure, measured in
significant figures rather than decimal places.

Two more process defects logged: user-facing stop paths do not route through
`kill_tree` (C13's fourth appearance — a manual stop left both the wrapper and
the interpreter running while the ledger kept growing), and the wall-outlier
detector cannot flag the first three tasks of a run.

`docs/DEFERRED.md` now carries twelve entries. The freeze held: everything
above was either a granted exception or a defect that would have invalidated
a measurement in flight.

## Unreleased — Phase 1: the tax measured, v3 rejected

`HARNESS_VERSION` remains **2**. v3 was evaluated and rejected on evidence;
nothing in this section changes what counts as a pass, so every v2 number
still stands.

### Measured

* **The harness tax does not generalize — 1 of 3 tasks.** Ladder R0/R1 on the
  3B: E03 unaffected (3/3 → 3/3), E04 collapses (3/3 → 0/3), E05 partial
  (3/3 → 2/3). The premise that a one-line prompt change would "substantially
  raise every 3B number" does not hold.
* **The mechanism was wrong and is now narrower and better evidenced.** It is
  **answer position**, not chain-of-thought suppression. Failing responses
  emit `key: value` *before* any working; passing responses emit the number
  *last*. E04's failure states `delta_v_m_s: 8860`, then writes the correct
  formula and the correct substitution and never evaluates them — post-hoc
  rationalisation anchored to a token autoregression cannot revise. Length is
  downstream: a response that commits to a number first has nothing left to
  write. **E05 is the within-task control** — same task, same rung, same
  prompt bytes, temperature 0, both behaviours observed, the answer-first
  sample wrong and the two answer-last samples right.
* **The tax is capacity-dependent.** `granite4.1:8b` scores **5/5** at R1
  where the 3B scores 0/5, on the identical 456-character prompt. Recorded as
  a capacity threshold. It costs 77–127 s against the 3B's 7.7 s — 10–16× the
  wall clock, on a benchmark whose only cost axis is seconds. Its shown
  working is not a faithful trace: it writes `sqrt(58873.6) = 242.6 m/s`, then
  "approximately 7.6 km/s", then lands correctly on 7663.2.

### Decided

* **v3 is not applied.** R4 0/5, R5 0/5 with the step-by-step instruction
  appended to the suite goal — the injection point the real change would use.
  Both decision routes agree: the fix does not survive the loop, and the tax
  it targets does not generalize. The "you would not want to ship a
  CoT-suppressing harness regardless of pass rates" argument was conditional
  on v3 surviving Phase 1, and it did not.
* **What v3 *did* do is recorded as a real effect that is not a pass.** It
  improved error **structure** 3.6× on the monotonic scorer with **no** change
  in pass rate: 11,185,784.5 (145689% out, score 0.077) became
  7909.544108195981 (3.09% out, score 0.279). That figure is
  `sqrt(mu/(6371000+400))` exactly — the model reads `altitude_km: 400` and
  adds **400 metres**, the suite's own km-to-m trap. By FINDINGS §4's
  criterion this makes the suite measure what it was built to measure. It
  still fails, and it is not tuned into a pass.

### Found — a fourth bug in each documented class

* **Instrument (4th): the ladder's R4 rung scores step allocation, not
  correctness.** R4 sets `max_steps=1`, so it measures *whether the model
  spent its one action on `run_code`*. Four of five samples spent it on
  `read_file(params.json)` — correct behaviour — and scored zero in 7.4 s. The
  bias is directional: "work through it step by step" is an instruction to
  take preparatory steps, exactly what R4 punishes. The published v2 R4 row
  carries the same defect. Found by applying the documented test — what would
  this check score for an agent that did the work perfectly but behaved
  slightly unusually? See SPEC-DELTA C16.
* **Process (4th): `reap_orphans()` kills healthy workers.** It matches on
  process name alone and `kill_tree()`s every `llama-server`, live or not;
  it killed the warm model three times during Phase 1, defeating
  `keep_alive`. This is the mirror image of the documented pattern — not
  "anything spawned outlives what spawned it" but "the reaper cannot tell a
  live child from an orphan". Because R0 runs first, R0's first sample always
  absorbs a cold load, so the published `45.7 s → 7.7 s` drop is partly
  cold-load and is not a clean effect size; the drop survives on warm samples
  (E04: 24.3 s → 12.0 s). `noise.py` calls the reaper once in preflight, not
  per task, so full-suite medians are unaffected. See SPEC-DELTA C17.
* **Temperature 0 is not byte-deterministic here.** Four counterexamples
  across both models on identical prompt bytes. The pattern is specific: the
  *first* sample of a block diverges, subsequent samples are byte-identical.
  E05's is a correctness flip, not a cosmetic one. Any plan that cuts repeat
  passes because "warm runs at temperature 0 are byte-identical" rests on a
  false premise; cold-start repeats are the right variance instrument. See
  SPEC-DELTA C18.
* **The 8B does not fit this machine alongside normal desktop use.** Measured:
  7.64 GB resident at the executor's `num_ctx: 16384`, against 3.6–6.4 GB free
  depending on what else is open — leaving 0.79 GB or less against a 2 GB
  house-rule floor. One Phase 1 model call took **2465 s** against a 600 s
  budget and manufactured a `budget_exhausted`; since that outcome is a time
  limit, memory pressure converts directly into apparent task difficulty. See
  SPEC-DELTA C20.

### Changed

* `bench/ladder.py` gains `--step-by-step`, which appends the reasoning
  instruction to the contract at R0–R3 and to the **suite goal** at R4/R5 —
  the goal deliberately, because that is where the real v3 change would land.
  The instruction is a named constant echoed into every run record; the
  earlier probe preserved its result but not its prompt.
* Every ladder run record now carries `harness_version`, `step_by_step`,
  `step_by_step_text` and `rungs`.
* **The two `HANDOVER.md` files are one.** `docs/HANDOVER.md` absorbs the root
  file's architecture map, module map, verifier table, traps, glossary and
  "the claim to actually make" as sections 17–21; the root file is deleted and
  all pointers repointed. Seven stale claims were dropped rather than merged
  and are listed in section 17 — six are ordinary drift, and one is
  **withdrawn rather than stale**: "3 tasks, 0/3 baseline → 3/3 with the
  calibration skill" predates the shared-workspace defect (C12) that
  manufactures exactly that result.
* `--rungs` is documented, along with every other ladder flag, including why a
  rung subset is a valid experiment and why the R3/R4 seam is not a
  like-for-like step.
* The documented venv path was wrong for a OneDrive checkout (`..\..\..\`
  lands on `OneDrive`, not the user profile). Now `%USERPROFILE%`.
* `cal.json` deleted — a tracked artifact of an aborted calibration whose own
  verdict was "insufficient / stub".

## Unreleased — the measurement

Everything in this section is instrument work. The system was already running
end to end; what was missing was the ability to trust a number it produced.

### Fixed

* **H30b was measuring which convention the model guessed, not physics.**
  The task never said which heliocentric speed to use, and its two sources
  disagree: `constants.json` gives `mars.orbital_v = 24077` (Mars's true mean
  speed, an orbit with e = 0.0934) while `sqrt(mu_sun/r2)` = 24131.5 (a
  circular orbit at r2). Both are correct facts about different models; the
  0.23% gap amplifies to 1.27% in the capture burn. Fixed by pinning the
  convention in the objective, **not** by touching the tolerance. A
  spec-following answer now scores 0.9995 through the real sandboxed
  verifier, where it previously scored 0.37 — which would have made the M7
  ratchet prefer noise over right answers.
* **`bench/run.py` never enforced a backend profile.** `Config.profile`
  defaults to `mixed` and `config.local.yaml` does not set the key, so the
  gate run — the most important measurement in the project — was the one
  command not checking what it was allowed to touch, and would have recorded
  `mixed` on a run that was in fact local.
* **The noise floor could not survive being killed.** Every result was held
  in memory and written once, after the final task, so a kill at hour three
  of a four-hour pass lost all of it. Results now append to a
  `.partial.jsonl` ledger, fsynced per row, with a signature-checked
  `--resume`.
* **A resumed report understated its own cost** — it counted only the tasks
  that process ran. Split into `wall_s_this_session` and `wall_s_all_runs`.

### Added

* `--split` on `bench/run.py paired`, so the contamination boundary is
  expressible. `check_contamination` only fires on a task named in a skill's
  provenance, and a hand-written skill names none.
* **Two-number calibration reporting.** `evaluate_skill` takes a `task_keys`
  subset, so the contamination-clean eval analysis is read off the *same*
  stored trials as the full-suite analysis — no extra compute. PRIMARY (all
  tasks, positive control) and SECONDARY (eval subset, underpowered) are both
  printed and written to `runs/calibration.json`, each carrying its caveat as
  a field on the number rather than a footnote.
* Every gate verdict now carries the full stat block — `n`, `b`, `c`,
  `discordant`, `lift_pp`, `p` — including `insufficient`, which previously
  reported only `n` and so hid which way an underpowered result pointed.
* `paired --resume`, skipping pairs with **both** arms recorded. A
  half-finished pair is deliberately re-run, because the analysis drops any
  pair missing an arm.
* `status_counts`, `ended_at_step_limit`, `ended_budget_exhausted` and
  `tier_pass_rates` on the noise report, so a run records *why* it ended.
  `max_steps` means the agent burned all six turns; `plateau` means more
  turns bought nothing; `budget_exhausted` means it ran out of **time**, and
  may not have been failed on the physics at all.

  **Corrected same-day:** this was first documented as "`budget_exhausted`
  has never fired and largely cannot", on the reasoning that `max_steps: 6`
  binds before the 600s budget. M16 then ended `budget_exhausted` at 705.6s
  in pass 1 — six slow turns on a medium task cross 600s of model time before
  the step limit. Both limits bind, and which one does is itself a signal.
* `tests/test_calibration_path.py` — the hours-long calibration driven end to
  end on scripted outcomes, including the case where the skill **hurts**.
* `tests/test_noise_durability.py` — including a real subprocess killed
  mid-pass.
* `tests/test_bench_suite.py` — the suite's own answers recomputed by an
  independent solver, and a check that all 30 verifiers reject a deliberately
  wrong answer.

### Changed

* `gate.min_paired_trials` 30 → 15. **This is not a weakening.** McNemar
  reads only discordant pairs, so n=15 with 10 discordant is exactly as valid
  as n=30 with 10 discordant. Holding `min_discordant_pairs` at 10 against a
  pool of 15 rather than 30 raises the bar from 33% of outcomes changed to
  67%. `min_discordant_pairs` is the load-bearing guard and must not be
  lowered; `min_lift_pp` is **inert** below n=160 and is not protection.
* Invariants 26–28 added, covering measurement-run durability and task-level
  trial counting.

### Known, and not yet done

* The noise floor has been run to **pass 1 of 3** only.
* The calibration skill has **never** been through the full gate on real
  models. The path is dry-run tested; it has not produced a real verdict.
* **No generated skill** has been produced or evaluated.
* The **Anthropic adapter is unverified** — written and unit-tested, never
  exercised against the live API. Do not describe it as working.
* The built wheel is **code-complete but not runnable standalone**: it
  carries all 64 modules and none of `policy.md`, `themes/`, `assets/`, or
  the config files, which the runtime resolves relative to the config's
  parent. Today the project is "clone and run from the directory", not "pip
  install and go".
* No `LICENSE`. That is a decision for the author, not a default.

## 0.1.0 groundwork — 2026-08-17 → 2026-08-24

* M1–M13 implemented from `spec/BUILD-SPEC.md` with its own acceptance tests.
* Sources restored twice after OneDrive data loss; the virtualenv now lives
  outside the sync folder.
* Runnable with no API key at all, on Granite 4.1 through a dedicated Ollama
  instance on port 11435.
* Control plane hardened — bearer token, Origin allow-list, DNS-rebinding
  protection — before being exposed anywhere.
* User-authored suites, policy and grounding moved out of files into a web
  console.
* **Seven components that had passing unit tests and zero call sites were
  wired into the runtime.** A test that a function works is not a test that
  the system calls it; `tests/test_wired_invariants.py` exists because of it.
* The 30-task `aero_calc` suite, generated from one table, with a
  drafting/eval split and independently recomputed answers.
* Enforced backend profiles: `--profile local` refuses cloud backends *and*
  provider credentials, and the profile is recorded on every run.
* `spec/CORRECTIONS.md` — errata for the original specification, which
  contained a model that does not exist, the wrong tokenizer, an API
  parameter that now returns 400, an inverted comparison contradicting its
  own test, and a config that trips its own assertion.
