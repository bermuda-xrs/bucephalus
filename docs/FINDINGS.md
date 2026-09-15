# Findings

> **The aerospace suite is archived.** As of 2026-09-05 `aero_calc` lives
> under `bench/suites/archive/`, `bench/tasks/archive/` and
> `bench/archive/decompose_aero.py`, and is no longer any tool's default —
> `--suite` and `--tasks` are now required arguments. Every result below
> remains reproducible from the archive. Nothing was deleted. See FINDINGS
> 1.13 for why it was retired.

What the numbers show, what they do not, and what would change my mind.

**Status: incomplete.** Phases 4–6 have not run. Sections marked *pending* are
placeholders with their acceptance criteria stated in advance, so the result
cannot be written to fit whatever comes out.

Hardware for everything below: Intel Core Ultra 5 225U, 12C/14T, **15.5 GB
RAM, CPU inference**. Models `granite4.1:3b` (3.4B, Q4_K_M) and
`granite4.1:8b`. Profile `local` — no cloud backend, no API key reachable.

---

## 1. What is actually established

### 1.1 The model can do the physics; the harness was eating it

The single most useful measurement so far, and it cost five minutes.

`granite4.1:3b` prompted **directly** with E01 — no agent loop, no file I/O,
no JSON envelope — 5 samples at temperature 0, all byte-identical:

| | |
|---|---|
| orbital radius used | 6,771,000 m — **R + altitude, correct** |
| answer | **7,668 m/s** |
| expected | 7,672.6 m/s |
| relative error | **0.06%** — tolerance is 0.5%, so 8× inside |
| verdict | **pass, 5/5** |

The same model, same question, inside the agent loop wrote **7,909,544** and
failed 5/5 — the radius trap plus a ~1000× scale error.

E01's goal is **115 words, of which 10 are the physics and 105 (91%) are I/O
contract**. The suite does not ask a 3.4B model to do orbital mechanics; it
asks it to write a *program* that reads JSON, computes orbital mechanics, and
writes JSON. Bucket D's shape agrees: five tasks returned an input constant
verbatim and two returned `0.0` — what a program looks like when the plumbing
was wired and the calculation never filled in.

**This retired the rebalance question.** Easier task variants would not have
touched a 91% plumbing overhead.

### 1.2 The first 10% was mostly instrumentation, not difficulty

Failure taxonomy over the original 30-task pass 1 (3B, pre-fix harness):

| bucket | n | |
|---|---|---|
| A — no output | 7 | |
| B — malformed | 1 | |
| C — budget/step ceiling | 2 | |
| D — wrong number | 15 | median relative error **100%**; 14/15 unstructured |
| E — looping | 2 | |

**13 of 27 failures (48%) never attempted the physics.** Two defects
accounted for them, both since fixed:

* the objective named the params file but not its **keys**, so the agent
  guessed (`mass_flow_rate` for `mdot_kg_s`, `thrust` for `thrust_n`) — 8
  tasks died on a `KeyError`;
* a stray `}` at the end of the `code` field produced a byte-identical
  `SyntaxError: unmatched '}'` — 10 tasks.

### 1.3 Defects found in the instrument itself

Each of these could have produced a publishable-looking number that was wrong.

* **The grader was inside the agent's reachable workspace.** `verify.py` held
  `EXPECTED` in plaintext and `artifact_scope` was the directory containing
  it. A generated skill saying "read verify.py" would have scored 100% and
  presented as the largest lift ever measured. Nothing exploited it only
  because a 3.4B model is not clever enough to try.
* **The workspace was never reset between arms.** `verify_program` grades
  files on disk, and both arms of a paired trial share one workspace, so the
  **control arm could be graded against the answer the skill arm had just
  written** — manufacturing exactly the lift the gate exists to detect.
* **Two measurement processes ran concurrently for 36 minutes.** Three tasks
  were measured under 2× CPU load; `budget_exhausted` is a time limit, so
  contention can *manufacture* that outcome.
* **An orphaned `llama-server` held 5.9 GB after its parent was killed.** One
  model call took **1499.8s for work that takes 88.1s** — an 18× slowdown, on
  a metric whose cost axis is seconds.
* **`verify_program` resolved paths against the process CWD**, so running a
  harness from anywhere but the project root scored a silent 0% on every task.

### 1.4 The 8B cannot follow the tag protocol

Attempting to remove the brace bug at source by switching to
`<code>…</code>` produced, on `granite4.1:8b`: 10 model calls, 6 parseable
actions, and **0 `<final>` emissions across 8 tasks**. No task produced an
answer, so there was no error signature to read at all.

| | reaches `<final>`? |
|---|---|
| 3B + JSON | ✅ |
| 3B + tags | ✅ |
| **8B + tags** | ❌ 0 of 10 calls |

The protocol is now per-backend, defaulting to constrained JSON. Same class as
the documented 8B deputy that could not emit `VERDICT:` at all.

---

### 1.5 The 8B does the work and does not say it is done — an operational hazard, not a benchmark artifact

D2, `granite4.1:8b`, 5 easy tasks. Four of five artifacts are **correct**:

| task | wrote | expected | ratio | |
|---|---|---|---|---|
| E01 | 7672.598648385013 | 7672.6 | 1.000000 | correct |
| E04 | 4233.875371812499 | 4233.9 | 0.999994 | correct |
| E05 | 12921.875 | 12922.0 | 0.999990 | correct |
| E07 | 287.73865345056953 | 287.7 | 1.000134 | correct |
| E03 | — | 5027.0 | — | no artifact |

**Zero errors.** Not clustered, not unstructured — none. The recorded pass
rate was **1/5 = 20%**; re-grading the same files under the sweep gives
**4/5 = 80%**. Three tasks wrote a correct answer and were scored as failures
purely because the model never emitted `<final>`.

**This is a real operational hazard, not a measurement artifact.** M13's
two-touch model is: approve a plan, receive a result. An agent that completes
the work and never announces completion **hangs** — it burns its step budget
and then reports failure, with the correct answer sitting on disk unread.
Under the shipped configuration that is a task the user is told failed, and
they would have to look in the workspace to discover otherwise.

So the sweep is **shipped behaviour, not benchmark-only**. It is in
`run_task`, which is the same loop the CLI and control plane use — there is
no benchmark-only code path, and adding one would mean the measured system
diverging from the shipped system again.

But the sweep treats the symptom. The underlying behaviour — an 8B that does
not terminate — remains, and it has a second face: the same model could not
emit `<final>` under the tag protocol *at all* (0 of 10 calls across 8 tasks),
while the 3B managed it under both protocols. Ruled out as a config artifact:
`--executor` overrides only `BackendConfig.model`, so temperature, `num_ctx`,
`num_predict`, thinking and protocol were byte-identical between the two runs.

`done_unannounced` is therefore reported as its own count everywhere, never
folded into `done`. If it dominates in a full run, the honest headline is not
"the 8B scores X%" but "the 8B does the work and cannot tell you it has".


### 1.6 The tax does not generalize, and the mechanism is answer position, not length

Phase 1, 2026-09-03. Ladder R0/R1, `granite4.1:3b`, 3 samples per rung,
temperature 0. Harness v2.

| task | physics | R0 | R1 | R0 chars | R1 chars | R1 wall | answer position at R1 | verdict |
|---|---|---|---|---|---|---|---|---|
| E03 | Mars escape velocity | 3/3 | **3/3** | 775 | 694 | 27-28 s | last | unaffected |
| E04 | Tsiolkovsky delta-v | 3/3 | **0/3** | 561 | 344 | 12-13 s | **first** | collapse |
| E05 | dynamic pressure | 3/3 | **2/3** | 626 | 362 / 685 | 15.6 / 30-33 s | s1 first, s2-3 last | partial |

**The tax reproduces on 1 of 3 tasks.** It is not a general property of the
output contract. E01 and E04 collapse; E05 collapses in 1 of 3 samples; E03
does not collapse at all.

**The mechanism stated in HANDOVER section 6 — "chain-of-thought
suppression", evidenced by response length collapsing 1155 to 236 to 32
characters — is a correlate, not the cause.** The discriminator is **where the answer appears**.

Every failing response emits `key: value` *before* any working. Every passing
response emits the number *last*. E04's R1 failure is the clearest case: it
states `delta_v_m_s: 8860`, then writes the **correct** formula and the
**correct** substitution, and never evaluates them. The working is post-hoc
rationalisation anchored to a token that is already in the context and that
autoregression cannot revise.

**E05 is the control that isolates this.** Same task, same rung, same prompt
bytes, temperature 0 — and it produced both behaviours across three samples.
The answer-first sample (362 chars) was wrong; the two answer-last samples
(685 chars) were right. That separates answer position from task identity,
from rung, and from prompt in a single within-task comparison. Length is
downstream of position: a response that commits to a number first has nothing
left to write.

This is a **better** finding than the one it replaces, and it narrows the
fix. The intervention that matters is not "make the model reason more", it is
"stop the model emitting the answer token first".

### 1.7 The tax is capacity-dependent: the 8B is unaffected

Ladder R1, `granite4.1:8b`, E01, 5 samples. The 3B scores 0/5 on this exact
rung, task and 456-character prompt.

| model | R1 correct | response | answer position | wall |
|---|---|---|---|---|
| `granite4.1:3b` | **0/5** | 236 ch | first | 7.7 s |
| `granite4.1:8b` | **5/5** | 1002 ch | last | 77-127 s |

The 8B keeps the orbital radius correct (6,771,000 m) and emits its JSON
after the derivation. **The R1 harness tax is a property of the 3.4B, not of
the scaffold alone.**

Two caveats belong with this number:

* **Cost.** The 8B pays 77-127 s for what the 3B does in 7.7 s — 10x to 16x
  the wall clock, on a benchmark whose only cost axis is seconds. "The 8B is
  unaffected" is not the same as "the 8B is the right choice".
* **The working is not a trace.** Mid-derivation the 8B writes
  `sqrt(58873.6) = 242.6 m/s`, then "approximately 7.6 km/s", then lands on
  7663.2, which is correct. Reasoning *presence* predicts correctness; the
  reasoning *shown* is not what produced the answer. Any later attempt to
  score these traces for quality rather than count them will be measuring
  something else.

### 1.8 The v3 fix does not survive the loop, but it does restructure the error

R4 and R5 with the step-by-step instruction appended to the **suite goal** —
the injection point the real v3 change would use — `granite4.1:3b`, E01, 5
samples. Criterion for applying v3 was >= 3/5.

**R4 0/5, R5 0/5. v3 was not applied. `HARNESS_VERSION` remains 2.**

**R4 is not a valid measurement — see section 1.9.** R5 (`max_steps=10`) is
the valid rung. One of its five samples is contaminated: a single model call
took 2465 s against a 600 s budget and ended `budget_exhausted`, the
documented contention hazard. 0 correct holds with or without it.

**What v3 did do, stated precisely because it is a real effect that is not a
pass:**

| | answer | relative error | score |
|---|---|---|---|
| v2 R5 | 11185784.5 | 145689% | 0.077 |
| v3 R5 | **7909.544108195981** | **3.09%** | **0.279** |

A 3.6x improvement on the monotonic scorer with **no change in pass rate**.

The v3 answer is `sqrt(mu / (6371000 + 400))` to full float precision: the
model reads `altitude_km: 400` and adds **400 metres**. The second failure
family, 250.12, is the same `r = 6371400` with a spurious 1000x. Both are the
suite's designed km-to-m trap.

Section 4 of this document says a working suite should show errors clustered
at recognizable factors rather than unstructured ones. By that criterion v3
makes the suite measure what it was built to measure — while still failing
it. **This is not reported as a pass and must not be tuned into one.**

### 1.9 A fourth instrument bug: R4 scores step allocation, not correctness

R4 sets `max_steps=1`. It is documented as "the full agent loop, one step
allowed" and read as answering "can it deliver this". What it actually scores
is *did the model happen to spend its single action on `run_code`*.

Observed across the five v3 samples: sample 1 spent its step on `run_code`
and produced an answer (score 0.143); samples 2-5 spent their step on
`read_file(params.json)` and produced **no artifact at all**, in 7.2-7.5 s.

Reading your input file before computing is correct agent behaviour. Here it
scores zero.

**The bias runs against the intervention under test.** "Work through it step
by step" is an instruction to take preparatory steps, which is exactly what
R4 punishes. R4 therefore cannot contribute to the v3 decision in either
direction, and the published v2 R4 row (0/5, 11185784.5) carries the same
defect — it should not be read as evidence that the tax survives into the
full loop.

This is the **fourth** instrument-class bug and it fits the documented
pattern exactly: the thing measured is adjacent to the thing claimed. It was
found by asking the question the pattern prescribes — *what would this check
score for an agent that did the work perfectly but behaved slightly
unusually?*

### 1.10 A fourth process bug: the orphan reaper kills healthy workers

`reap_orphans()` matches on process name alone (`ORPHAN_NAMES =
("llama-server",)`) and `kill_tree()`s every match, live or not. It killed
the warm model three times during Phase 1, between ladder runs.

This is the **mirror image** of the documented process-class pattern.
Class 2 is "anything spawned outlives what spawned it"; this one is "the
reaper cannot tell a live child from an orphan".

**Consequence for the published ladder.** R0 always runs first, so R0's first
sample always absorbs a cold model load (~58 s on this hardware). That
inflates the R0-to-R1 *wall-clock* drop which section 1.1 and the handover
cite as evidence of suppression. The drop survives on warm samples — E04 goes
24.3 s to 12.0 s — so the finding stands, but the published 45.7 s to 7.7 s
for E01 is partly cold-load and should not be quoted as a pure effect size.

**Blast radius is limited:** `noise.py` calls `reap_orphans()` once in
preflight, not per task, so a full-suite median is unaffected. Only the first
task of a run is exposed.

### 1.11 Temperature 0 is not byte-deterministic on this stack

Four counterexamples, across both models, on identical prompt bytes:

| observation | first sample | subsequent samples |
|---|---|---|
| E05 R1 (3B) | 362 ch, **wrong** (8775.75) | 685 ch, **right** (12918.75) |
| E04 R0 (3B) | 537 ch | 561 ch |
| E01 R1 (8B) | 7674.6, 1097 ch, 127.1 s | 7663.2, 1002 ch, ~78 s |
| E01 R5 (3B) | 7909.54 | 250.12, 250.12 |

The pattern is specific: **the first sample of a block diverges; subsequent
samples are byte-identical to each other.** Divergence is tied to the first
call after a model load or a rung change, not to general chaos. `ladder.py`
rebuilds the prompt identically per sample and `complete()` sends an
identical body, so this is not a prompt artifact.

**This contradicts a premise the measurement plan rests on** — that warm-cache
runs at temperature 0 are byte-identical, so within-process variance is near
zero and repeat passes carry no information. As stated, that does not hold.
The right instrument is **cold-start** repeats, because cold start is
precisely the condition that produces the divergence. `n=1` per task inherits
this variance, and the first task of any run is the most exposed.

---


### 1.12 The reference measurement: 24.1% raw, 28.0% timing-clean

The first complete 30-task measurement in the project's history.
`granite4.1:3b`, harness v2, randomised task order (seed 1420531403),
3.32 h. Evidence: `docs/evidence/phase2a-2b-analysis.json`.

**Two denominators, and neither is "the" answer.**

| | | |
|---|---|---|
| **raw** | 7 / 29 | **24.1%** |
| **timing-clean** | 7 / 25 | **28.0%** |

They **straddle the 25% band floor** — raw below it, timing-clean above.
The entire difference is four tasks (M16, H30, H24, H27) whose failure mode
is `budget_exhausted`, a **time** limit, measured with a clock the instrument
itself flagged as untrustworthy (3.18×, 5.79×, 4.04×, 5.15× the run median).
SPEC-DELTA C6 says that outcome is contention-manufacturable. The
conservative figure is raw, and the branch below uses it.

H26 was also flagged (3.51×) but **kept** in the timing-clean denominator:
`max_steps` is a step limit, which slow timing cannot manufacture. Its
wall-clock is untrustworthy; its pass/fail is sound.

| tier | passes | rate | which |
|---|---|---|---|
| easy | 3/10 | 30.0% | E03, E05, E06 |
| medium | 3/11 | 27.3% | M14, M15, M19 |
| **hard** | **1/8** | **12.5%** | H25 |

`done` 7 · **`done_unannounced` 0** · `max_steps` 1 · `budget_exhausted` 4 ·
median 252.0 s · p90 941.2 s · excluded 1 (M11) · machine faults 2 · **no
`crashed` rows** · worker refreshes 6 totalling 87.9 s (0.7% of the run).

**`done_unannounced` is zero, and that is a behavioural finding.** On the 8B
it was the *dominant* failure mode — correct answers written and never
announced (1.5). The 3B terminates properly. Its failures are wrong answers,
not unannounced right ones. Two models, byte-identical config, opposite
failure signatures.

### 1.13 The failures are unstructured, so no skill was measurable here

**This is a result, not the absence of one.**

Of 19 numeric failures, **3 decompose to a declared trap** (2 strictly, 1
ambiguous within a single family). B/A = **10.5%** strict, **15.8%**
inclusive — against a 50% threshold.

What the other 16 look like:

| task | written | expected shape |
|---|---|---|
| H28 | **−19115.85** | escape velocity |
| H23 | **−2038.86** | delta-v |
| M17 | **−779.93** | — |
| M21 | 5.32e12 | — |
| M22 | 4.31e10 | — |
| M13 | 5.23e-23 | — |
| E08 | 9.67e-04 | — |
| M16 | **0.0** | — |

A **negative escape velocity** is not a units confusion and not a wrong-body
error. It is a program that ran and never implemented the physics.

**The consequence for the research question.** A skill repairs *structured*
error. A units-discipline procedure can fix "read km as m"; nothing in a
written procedure fixes a program that never implemented the physics. So on
this substrate **no skill could have been measured to help** — not because
skills do not work, but because there was nothing for one to repair. That is
a property of the **model–task pairing**, not of skills.

**And this reproduces the original bucket-D taxonomy** (14 of 15 unstructured,
median relative error 100%, section 1.2) on a harness where the plumbing
defects that explained the first 10% are *gone*. The explanation changed; the
failure shape did not. That is the substantive part.

**Scope:** one model, one suite, one iteration.

### 1.14 Trap decomposition is a cheap pre-test for skill measurability

The transferable result.

**The method.** Before spending hours on paired trials, decompose the
failures against the traps the suite declares. If the failures do not
decompose, no written procedure has a handle on them, and the paired trial
cannot produce discordant pairs **regardless of how good the skill is**.

**The cost asymmetry.** Minutes of analysis against a 4+ hour experiment
avoided — and, more importantly, against a result that would have been
uninterpretable. A gate that rejects everything and a gate facing a substrate
with no structured error produce the same output.

**The `coverage.py` contrast, which sharpens it.** Coverage predicted an
upper bound of **20/30** tasks in scope. The measurement found **3**.

Coverage matches a skill's vocabulary against a task's **declared** trap. It
cannot know whether the model actually *commits* that trap. **Declared-trap
coverage is not measured-trap incidence, and only the second bounds the
experiment.** Coverage answers "could this skill in principle speak to this
task"; decomposition answers "did the model actually make the error the skill
addresses". The first is a property of the suite and the skill; the second is
a property of the *model on that suite*, which is what a paired trial
measures. Recorded as a limitation of `coverage.py` in DEFERRED D12.

**Scope: this is a claim about method, not about skills generally.** One
model, one suite, one iteration. What transfers is the pre-test, not the
verdict.

### 1.15 Disclosure: the decomposer was improved after seeing the data

Four defects were fixed in `bench/decompose.py` *during* Phase 2B, after the
failure values were visible. That is a researcher-degrees-of-freedom concern
and it is stated here rather than left to be noticed.

**The defence is directional, and it is checkable.** Every one of the four
fixes moved B **upward** — from **1 to 3** — i.e. each pushed *against* the
STOP, and the STOP held anyway. Fixes that make a conclusion **harder** to
reach, applied **before** the conclusion is drawn, strengthen it rather than
weakening it. Had any fix moved B downward, this disclosure would read very
differently.

The four:

1. `ZeroDivisionError` on a written `0.0` (M16). A written zero is a real
   outcome — the original bucket D had two — and it crashed the tool outright.
2. `TypeError` on a non-numeric expected value. H27 expects
   `bad_row_indices = [12, 21, 30, 34]`; it is an anomaly-detection task,
   structurally outside a numeric decomposer's scope, and is now reported as
   its own category rather than forced.
3. **The residual gate sat below the rounding floor of its own inputs, for
   the second time.** See below.
4. **Body swaps never crossed with radius formation.** E10 wrote
   `sqrt(mu_earth/R_earth)` — Earth's constants *and* the altitude dropped —
   and no candidate could reproduce it, because the altitude was only ever
   applied to the swapped body.

**The residual gate, stated so the number is reproducible rather than chosen.**

The gate is bounded on both sides by quantities that can be **computed**:

* **above** — the rounding floor of the tabled `expected`. Candidates are
  built as `expected × ratio`, so the tightest achievable residual is the
  rounding error of `expected` itself.
* **below** — the separation between distinct candidate traps. For E01,
  "altitude ignored (r = R)" and "km read as m (r = R + 400)" differ by only
  **3.1e-05**, because 400 m on a 6,371,000 m radius barely moves the answer.

A single global gate cannot satisfy both across tasks, because **the rounding
floor varies per task**: E03's Mars escape velocity is tabled `5027.0`
(floor ~9.4e-06) while E07's specific impulse is tabled `287.7`
(floor ~1.7e-04) — an order of magnitude apart. A global 2e-5 hid E07's
**exact** `thrust/mdot` hit at residual 1.34e-04, which is the g₀ trap E07
explicitly declares, reported as unclassifiable *because of the tolerance
rather than the data*.

So the tolerance is **per-task: three half-ulps of the last significant
figure**, measured in **significant figures rather than decimal places** —
the suite writes some values as `7.138e6`, which `repr` renders `7138000.0`,
and a decimal-place count would read that as far more precise than it is.
Where the two bounds ever cross, no single gate separates the candidates, and
the `ambiguous` classification reports both rather than implying a precision
the data does not carry (this is what E10 returns).

The acceptance guard was never relaxed: the five no-trap tasks return
`unclassifiable` under every probe, held by
`tests/test_decomposer_does_not_invent.py`.

### 1.16 The 2C branch, recorded and superseded

Raw **24.1%** is below the 25% floor. Buckets A and B are near zero: **2**
tasks produced no value at all (H30, H24), both `budget_exhausted`.

By the pre-registered branch table that is the **"mean <25% and A+B near
zero → genuine difficulty at this tier"** cell, whose prescribed action is to
**rebalance the suite**.

**That action was not taken.** It was superseded by the decision to archive
the aerospace suite (Phase Q). Rebalancing a suite that is being retired
would spend hours tuning difficulty on a substrate that 1.13 shows cannot
support the measurement in the first place — the problem is not that the
tasks are too hard, it is that the failures are unstructured.

Recorded here so the branch is visible and the deviation is explicit rather
than silent.

---

## 2. What these numbers do **not** show

* **Nothing about hybrid routing.** `config.local.yaml` maps the *cloud* slot
  to a local model. There is one tier, `choose()` returns it on the first
  branch every call, and escalation-by-disagreement has no call site.
  Measurement of two tiers is two *independent arms*, not a routed system —
  the 8B alone is 7.72 GB resident at 16k context against 15.5 GB total, so
  co-residency is not possible and a routed run would measure model swapping.
  **Routing is implemented, tested, and unexercised on this hardware.**
* **Nothing about cost in dollars.** Every call is $0.0000. The cost axis is
  wall-clock seconds; the dollar column is retained and labelled *n/a under
  local profile*.
* **Nothing about the deputy.** `auto_review` is off in every benchmark run,
  deliberately — an LLM in the decision path is variance in the signal being
  measured. The measured system is not the shipped system, and the report
  header names every disabled component.
* **Nothing about prompt-cache benefit.** `cache_read_tokens` is populated
  only by the mock backend; across 566 real local calls it is 0. Ollama has no
  prompt-cache accounting, so the ratio is *unverifiable locally* rather than
  zero. Prefix stability itself is enforced by `PrefixGuard`.
* **M7's ratchet is barely exercised.** The scorer is now monotonic in
  `log(rel)` so the ratchet has an ordering to climb — but the agent receives
  only a scalar ("score 0.00"), with no key, direction or magnitude. Of 30
  tasks in the original pass, **exactly one ever moved its score**. The
  gradient fixes the ratchet's *ordering*; it does not give the model an
  actionable observation, which is what refinement needs.
* **M7.4's anti-gaming veto never fired, and is retired with M11 (1.27).**
  Nothing recorded the gap it read. For a `verify_program` suite every check
  is held out, so the gap is undefined. Every promotion now says "not
  applicable - every check is held out, the grader is unreachable (SPEC-DELTA
  C1)"; it used to say `"NOT MEASURED"`.

---

## 3. Pending

* **3.1 Noise floor, both arms** — *pending Phase 4.*
* **3.2 Calibration** — *pending Phase 5; ready to run.* D35 option (a),
  2026-09-15. The control is `si_first_orbital_arithmetic`, drafted from the
  15 drafting tasks and scored on the 15 eval tasks only (`--split eval`). Its
  caveat is printed from its provenance, in the same paragraph as the number:
  drafted from 15 tasks, none of them scored. The drafting model's earlier
  sessions on this project mean prior exposure to the eval split cannot be
  ruled out. It is still a **positive control on the detector**, not a claim of
  generalization.

  **It can fire only on a large effect.** At n=15 the gate needs at least 10
  discordant pairs, 9 of 10 favouring the skill (`config.local.yaml`), so the
  skill must change about two-thirds of outcomes. A null result there says the
  effect was not huge. It does not say the detector is blind.

  `aero_units_discipline` declares the whole suite, truthfully, and is refused
  on all 30 tasks. It is kept as the record of the 10-pair run of 2026-08-27.
* **3.3 Module ablation** — *pending Phase 5.3.* The highest-value single
  measurement here. If lift collapses without module 3 (the magnitude check),
  the skill works by supplying the external observation the bare agent lacks —
  a mechanism, not a score.
* **3.4 M11 generated skills** — **RETIRED UNANSWERED, 2026-09-15 (1.27).** The
  published baseline is **−1.3pp** for self-generated skills. This project had
  planned 0 of 3 promoted as the expected result and a valid finding. It was
  never run. 1.27 says what would reopen it.

---

## 4. What would change my mind

* **On "the harness was eating the model":** a post-fix pass 1 whose bucket A+B
  count stays high. That would mean the two defects were not the cause and the
  I/O contract is still the task.
* **On "the suite measures physics":** bucket D staying 14/15 unstructured
  after the fixes. Traps that never get reached are decoration; a working suite
  should show errors clustered at recognizable factors (×1000, ×9.81, ×57.3,
  radius-vs-altitude).
* **On the calibration being a positive control:** rejection with many
  discordant pairs pointing the wrong way. That would mean the skill hurts, and
  *which* tasks it hurt would be more interesting than any lift.
* **On the instrument being trustworthy at all:** a promotion carrying
  `gaming_check: "NOT MEASURED"` alongside a large lift on a suite whose grader
  the agent can reach. That combination has already been possible once here and
  was closed only after it was looked for deliberately.
* **On small-model viability:** arm_3b landing near 0% while arm_8b clears 25%
  would say the local tier's scope in the spec — "summarizing, sorting,
  extracting" — is right, and that this task family was always the wrong ask
  for a 3.4B.

---

## 5. Reading order, by evidential strength

The findings above accumulated chronologically. This is the order to read
them in if you want the strongest claims first.

| # | Finding | Where | Strength |
|---|---|---|---|
| 1 | **Answer position, not response length, discriminates correctness** | 1.6 | Strongest. E05 is a natural control — same task, same rung, same prompt bytes, temperature 0, both behaviours observed. Warm-only timings 39.8 → 6.7 → 1.5 → 2.8 s. The R1→loop correspondence is **n=4 distinct tasks, each observed twice with identical outcomes**: replication, not extension |
| 2 | **The tax is capacity-dependent** | 1.7, 1.12 | Strong. 3B 0/5 vs 8B 5/5 at R1. Two caveats travel with it: the 8B costs 10–16× the wall clock, and its shown working is **not** a faithful trace of what produced its answer. Plus the behavioural split from 2A — `done_unannounced` was the 8B's dominant failure mode and is **zero** for the 3B |
| 3 | **The reference measurement** | 1.12 | Solid, single run. 24.1% raw / 28.0% timing-clean, both denominators, the straddle of the 25% floor, per-tier, and four `budget_exhausted` failures whose timing the instrument itself flagged |
| 4 | **Failures are unstructured, so no skill was measurable** | 1.13 | Solid. 3 of 19 numeric failures decompose to a declared trap. A skill repairs structured error; there was nothing here to repair |
| 5 | **Trap decomposition as a pre-test for skill measurability** | 1.14 | Methodological, and the transferable one. Scope: one model, one suite, one iteration |
| 6 | **What was not measured** | 2, 3, DEFERRED | — |

**Not measured, restated plainly:** no skill has ever been through the gate;
M11 (the generator) never ran, and was retired unanswered (1.27); the 8B does not fit this hardware at the
harness's context length; the in-loop token estimator is uncalibrated *and*
invariant 11's context ceiling budgets against it (recorded usage is the model
server's own count - corrected 2026-09-15; it said every token figure was an
estimate); and the
twelve entries in `docs/DEFERRED.md`.

---

## 6. Method — how the instrument was corrected

Separate from the findings, because this iteration's clearest output is not a
number. Eleven defects were found across two classes, and the ones below are
the episodes where the *process* caught what judgement missed.

### 6.1 The leak, and the rule that stopped a misdiagnosis landing

Phase 2A aborted at task 23 of 30 on a memory-suspect count. The commit ratio
across those 23 tasks:

```
1.231 1.231 1.240 1.253 1.255 1.280 1.292 1.296 1.315 1.316 1.326 1.335
1.354 1.366 1.375 1.377 1.377 1.381 1.391 1.400 1.409 1.415 1.421
```

Twenty-three consecutive readings, **not one decrease**. Free memory over the
same span oscillated 0.44 → 2.34 → 1.41 GB, so free memory alone could not
see it.

**The misdiagnosis.** Mid-run I judged the flags false positives — "too tight
by roughly 0.05", "three of these five are false positives" — because the
flagged tasks were not slow. They were not slow *yet*.

**What stopped it landing** was a rule, not an insight: *do not retune a
threshold mid-run when loosening it would conveniently avoid an abort.* The
analysis that would have overridden the rule was the thing that was wrong.

**The isolation measurement**, which settled it:

| state | committed | `llama-server` RSS |
|---|---|---|
| no model | 15.44 GB | — |
| fresh 3B, 16k | 18.92 GB | 3.41 GB |
| after 23 tasks | 22.12 GB | 4.86 GB |
| after reload | 18.92 GB | 3.41 GB |

The harness was innocent — the ratio read **1.431 after the Python process
was killed**, higher than while it ran. ~139 MB/task, fully reclaimed by a
reload. Confirmed independently at 141 MB/task by direct RSS measurement.

### 6.2 The signal that detected the leak was the wrong controller

The commit ratio *found* the leak and was load-bearing in doing so. It is
useless as the per-task flag. Post-fix it read 1.278–1.634 across ten healthy
tasks, so the old "pathological" anchor of 1.574 sat **inside** the healthy
range.

Replayed over the L6 verification run:

| rule | flagged |
|---|---|
| commit ratio ≥ 1.40 | **8 of 10** — including a task with 2.52 GB free |
| wall clock > 3× running median | **1 of 10** — exactly the one with a genuine 17× slowdown |

A detector and a controller are different jobs. The same signal can be
excellent at one and useless at the other.

### 6.3 The stall wiring: correct at both ends, caught one level too low

The stall detector worked and the `machine_fault` handler worked. Two Phase 2A
attempts still aborted, because the handler sat *below* a generic
`except BackendError`, so a stall was converted to `crashed` and scored as a
task failure before the handler could see it.

**A test asserting that `ModelStall` gets raised would have passed throughout
the entire defect.** The rule that follows: *when a check fires and the
outcome is still wrong, verify the path from check to outcome, not the check.*

The fix was not to improve recognition. An earlier guard tried to *recognise*
a machine cause in the row — looking for a `stalls` key or "timed out" in an
error string — and a crashed row carrying neither went straight through it.
Recognition was the part that kept failing, so every machine event now has its
own terminal status and `crashed` is simply unreachable for one.

### 6.4 The residual gate below its own inputs' rounding floor — twice

The decomposer's match tolerance was set to 1e-6, then 2e-5. Both were
**below the rounding error of the values being compared**, so entire families
of correct decomposition could never match — a check that could not have
matched anything, announcing nothing.

* 1e-6 hid E03's exact `sqrt(2·mu_mars/R_earth)` hit at residual 9.36e-06.
* 2e-5 then hid E07's exact `thrust/mdot` hit at residual 1.34e-04 — the g₀
  trap E07 explicitly declares.

The floor is **per-task**, because the suite tables values to differing
precision: `5027.0` (floor ~9.4e-06) against `287.7` (floor ~1.7e-04). The
gate is now three half-ulps of the last significant figure, measured in
**significant figures rather than decimal places** — `7.138e6` reprs as
`7138000.0`, and a decimal count reads that as far more precise than it is.

### 6.5 Injection over observation

Stalls occurred roughly three times in sixty tasks. A five-task shakedown
would almost certainly have seen none, and "no stall occurred" is not evidence
a handler works.

Injecting the condition found **three defects a real run had not surfaced**:
an unregistered event type that crashed the handler on its first use, the
generic-catch ordering above, and a transport timeout path that produced no
stall record at all.

*Prefer injecting a condition over waiting to observe it. Absence of a rare
event is not evidence a handler works.*

### 6.6 Instruction-level errors

Recorded beside the code defects, because they are the same class of mistake
and were caught the same way.

| Error | How it was caught |
|---|---|
| The "unstructured" premise — v2's 11185784.55 was assumed not to decompose | Decomposing it. It is `sqrt(mu/(R + alt_km))` × the escape-formula and ×1000 decorations; v2 and v3 share an **identical** radius error |
| The leave-one-out ablation design | Noticing the three skill modules are **redundant** against the observed error, so removing any one leaves the others compensating. Leave-one-in measures sufficiency; leave-one-out measures only necessity-given-the-others |
| "The uncalibrated tokenizer does not corrupt any result" | Invariant 11's context ceiling **budgets against that same estimator**, so it is a live safety consequence, not only a reporting caveat |
| A brief referenced but never supplied | Refusing to execute it rather than reconstructing it from context |
| An abort threshold conflating recovered faults with lost data | Noticing that H23 recovered and produced a valid result while M11's data was lost, and that counting them together aborts a healthy run on evidence that recovery worked |

The pattern across all five: **a stated criterion that measured something
adjacent to what it claimed.** It is the same shape as the six instrument
defects, and it does not stop being that shape because it appeared in an
instruction rather than in code.

---

## 7. Pareto — one arm, and why the second was cut

**One point, plotted honestly, beats two with a void one.**

| arm | median s/task | pass rate | harness | toolset |
|---|---|---|---|---|
| `granite4.1:3b`, bare | **252.0 s** | 24.1% raw / 28.0% timing-clean | 2 | 0 |

**Why there is no second arm.** The 8B was the intended comparison and it was
cut on measurement, not preference: it is **7.64 GB resident** at the
executor's `num_ctx: 16384`, against 3.6–6.4 GB free depending on what else is
running. Loading it leaves ~0.8 GB against a 2 GB floor. A full 8B pass would
have paged throughout, and the *only* cost axis this project has is wall-clock
seconds — so the second point would have been a measurement of the pagefile.

Recorded as an evidenced correction to the spec's laptop-tier assumption
(SPEC-DELTA C20), not as a gap.

**Two caveats on the point that is plotted.** Its median excludes nothing, but
five tasks carried wall-outlier flags, and four of those are `budget_exhausted`
— failures decided by a clock the instrument distrusted. And the dollar axis
is `n/a`: every call cost $0.0000 under `--profile local`.

---

### 1.17 Memory tiers differ in what content is BELIEVED, not in what survives

Phase 3 refuses untrusted-derived content from tier 1 rather than queueing it
for approval, which was the brief. The reasoning generalises past this one
decision and is worth stating on its own.

Tier 1 is rendered into the stable prefix. Tier 2 is retrieved on demand and
comes back stamped `UNTRUSTED`. A fact learned from a web page can live in
either. So the tiers **do not differ in whether the content survives — they
differ in whether it survives as something the model is told to believe.**

That reframing is what settles the queue-versus-refuse question. Queueing
sounds like the moderate option and is not: it puts a human one click from
promoting attacker-controlled text into the prefix of every future session,
judged on one line of a tool-call preview, which is precisely the surface a
good injection is written to survive. Refusing costs nothing, because the
content is still available through the tier that labels it.

The same test applies to any future store: not "can this content be kept?"
but "if it is kept, is it data or is it voice?"

---

### 1.18 Minimum budget overshoot is one step, and one step scales with the tool manual

The budget is checked BETWEEN steps. So a task cannot spend less than one
step, whatever budget it was given, and one step includes the whole
cache-stable prefix — which contains the tool manual.

Measured 2026-09-07: a $0.001 budget with 16 tools registered produced
`budget_exhausted` after exactly one model call, having spent $0.0100 — a 10x
overshoot. The same test spent under $0.005 when the manual held 8 tools. The
mechanism was correct in both cases; the manual doubled.

**Why this is a finding and not a test repair.** The assertion that surfaced
it read `cost_usd < 0.01`, a proxy for "it did not run away". Raising the
number would have restored green and hidden the property. The test now
asserts the mechanism — exactly one model call against six scripted actions —
which is what was actually being claimed.

**Practical scope.** `default_task_budget_usd` is 0.50, two orders above one
step, so nothing binds today. It binds for any caller passing a budget near a
single step, who gets one step regardless of what they asked for, and the
threshold moves every time a tool is added. Tools are not free even when
unused: they are in the prefix of every call.

---

### 1.19 A scraped source needs "parsed nothing" and "matched nothing" kept apart

`web_search` reads DuckDuckGo's HTML endpoint, because every search API worth
using needs a key and a key makes `--profile local` refuse to start. That
makes retrieval a scraper, and a scraper breaks silently: the markup changes,
the parser finds nothing, the caller sees zero results.

Zero results from a working parser and zero from a broken one are
indistinguishable to the caller and have OPPOSITE fixes — one means try a
different query, the other means fix the parser. So the distinction is
carried as a failure KIND (`parse_failure` against `transport_failure`)
through the tool result, into `provenance.retrieval`, and onto the hash chain
as `tool.search_failed`.

**Why it is worth the machinery.** Without it a broken scraper reads as a
topic with no coverage, and every later search in the run looks like an
honest miss. The same shape as scoring a machine stall as a task failure: an
instrument malfunction wearing the costume of a result.

**Consequence for G4, noted not built.** `generate_skill` grounds on
`ctx.research_retrieve(...)` and drafts from whatever comes back. A grounding
step that returned nothing because the parser broke would produce an
UNGROUNDED SKILL THAT LOOKS GROUNDED — provenance would record sources
requested and zero retrieved, with nothing marking the difference between
"nothing exists" and "retrieval malfunctioned". DEFERRED D19; it belongs with
Phase 12, where skill provenance is the subject. *2026-09-15: the generator is
retired (1.27), and closing D19 is one of the three conditions for reopening
it.*

---

### 1.20 G6 is still "measured in anger", and the resume it rested on was a different mechanism

`RELEASE.md` marks G6 (durability) measured because a killed run resumed from
the fsynced ledger. D20 then found that `integrity.resume()` — the function
that verifies a chain before a task continues — had no caller, which raised
the question of whether that claim rested on an unverified resume.

**It did not, and this is verified rather than inferred.** Across all 283
tasks in the live database, ZERO entered `RUNNING` more than once.
`set_status(RUNNING)` is the first statement in `run_task`, so a second call
on the same task id always leaves a second `task.status_changed` event; the
absence of any means no task was ever continued after interruption.

**The two mechanisms are different things sharing a word.**
`bench/noise.py --resume` skips ledger rows already written and creates NEW
tasks for the remaining specs. It never continues an existing task, so
`integrity.resume()` was never the thing being exercised. G6's claim is about
the LEDGER surviving a kill, which it does and which the test proves; it was
never a claim about chain-verified task continuation.

So the finding is not that a published number is wrong. It is that **the word
"resume" covered two mechanisms, and the healthy one kept the broken one
hidden** — every textual search for `resume` found the CLI flag. That is what
made the dead-code audit's receiver-aware fix pay for itself in seconds.

---

### 1.21 M7.4 is one rule of four actually enforced

The anti-gaming rules exist to police generated skills — a skill fitted to
the checks it can see is the failure this whole apparatus expects. Their
current state:

| rule | mechanism | enforced? |
|---|---|---|
| 1. different authors for skill and its tests | `assert_different_authors` | **yes** |
| 2. visible/held-out gap recorded | `record_holdout_gap` | **no — no caller** |
| 3. isomorphic verification | `verify_isomorphic` | **no — no caller** |
| 4. trusted-tier actions never auto-reviewed | the gate | **yes** |

Rules 2 and 3 are inert: written, unit-tested, and never invoked. So no
verification in this project has ever been cross-checked against a second
verifier, and no promotion has ever carried a measured visible/held-out gap —
every one records `gaming_check: NOT MEASURED`.

**Why they are grouped here rather than left in DEFERRED.** Individually each
is a deferred item. Together they are a scope statement about the whole
verification layer: **half of M7.4 has never run**, and the half that has not
is the half aimed at generated skills specifically.

**The gate, same shape as D19's.** The first real `generate_skill` run must
not precede M7.4 being genuinely enforced. A generated skill is precisely what
rules 2 and 3 exist to police, and promoting one on the strength of a gate
that is half inert would produce exactly the result the gate was built to
prevent — with a provenance record saying it passed.

Three of the project's own guarantees have now been found tested into looking
alive: `record_holdout_gap`, `integrity.resume` (fixed, D20) and
`verify_isomorphic`. The pattern is not carelessness about testing; it is
that a unit test proves a function works and says nothing about whether the
system calls it. `test_wired_invariants.py` exists for this and did not cover
these three.

**2026-09-14: the gate holds in code.** `generate_skill` raises
`GenerationGated` until the change that wires rules 2 and 3 flips
`M74_RULES_2_3_WIRED`. Rules 2 and 3 were not wired here: rule 3 needs a
choice of which verifier pairs are isomorphic for a task, and rule 2 a
visible/held-out partition that only the never-run generator produces - design,
not a line (D22). The scope statement is in README and RELEASE.

**2026-09-15: retired, not wired.** Rules 1, 2 and 3 policed generated skills
and nothing else, so they left with the generator (1.27). Rule 4, trusted-tier
actions never auto-reviewed, stands. What guards a hand-written skill against
fitting its checks is structural: the grader is unreachable (C1).

---

### 1.22 A word that covers two mechanisms makes the working one an alibi

`integrity.resume()` — chain verification before a task continues — had no
caller for the life of the project. It was not hidden by obscurity. It was
hidden by a NAME COLLISION with a healthy mechanism:

* `bench/noise.py --resume` skips ledger rows already written and creates new
  tasks for the rest. It works, it is tested, and it is exercised on every
  long run.
* `runlock.py`'s docstring discusses resuming.
* the module's own docstring names the resume protocol.

So every textual search for `resume` found something real, working, and
irrelevant. The dead-code audit credited all of them until its reference rule
was made receiver-aware.

**The general hazard:** when one word names two mechanisms, the working one
is an alibi for the broken one. Every grep confirms the word is alive; none
of them distinguishes which thing is alive. This is the same shape as the
adjacency family — a check answering a question adjacent to the one asked —
moved from code into vocabulary.

**Where to expect it next:** `--resume` versus `resume()` is now separated.
Remaining candidates in this tree are `verify` (chain verification, task
verifiers, and plan-pin verification are three different things sharing a
verb) and `check` (capability gate, exposure, verifiers). Both are worth
naming precisely before the next mechanism is added under one of them.


### 1.23 Two walls answer two questions, and a third question neither asks

The capability gate (M8.2) judges a tool call by the TAINT of its arguments:
where did these bytes come from? Exposure (Phase 5, B3) judges it by
AUTHORITY: may this agent call this tool at all? When Phase 5 landed it was
reasonable to ask whether the second wall was redundant with the first. It is
not, and A4 produced the case that shows exactly why.

**The taint wall stops the attacker's bytes. It does not stop the attacker's
intent, laundered through the model's paraphrase.** Taint is computed from
provenance - an argument is untrusted if it appears verbatim inside something
the task read from outside. That is what defeats the classic injection: the
attacker's address is untrusted because it was quoted from their page. But a
message that says "write a script that lists the home directory and reads the
key file" does not need to be quoted. The model writes the script itself, in
its own words, quoting nothing - and model output derived from the goal is USER
taint, inside `run_code`'s ceiling. The injected INSTRUCTION succeeds while
every injected BYTE is refused.

Exposure is the wall for that case, because it never looks at the arguments. A
channel task is denied `run_code` whatever it passes; no paraphrase changes a
refusal that does not read what was said.

| an attacker controls... | taint (M8.2) | exposure (B3) |
|---|---|---|
| bytes the agent will copy into an argument | **stops it** | not consulted |
| which tool the agent reaches for, via a paraphrase | does not see it | **stops it**, if the tool is denied |
| the content of an ALLOWED tool call, via a paraphrase | does not see it | does not see it |

The third row is the honest limit. A channel task may still `write_file`, and
it may write the attacker's intended content in the model's own words. Neither
wall can see that, because both are rules and the gap is in meaning. What
stands there is the third wall: a channel task never auto-proceeds (invariant
65), so a write whose content was shaped by a stranger is looked at by a human.
Tier-1 memory gets both mechanical walls at once - exposure denies it to a
channel task, and its USER ceiling refuses anything quoted - because it is the
one write whose content enters every future prompt.

**Consequence for anyone adding a denial:** justify it by the paraphrase gap,
not by the taint table. A denial that restates a taint ceiling adds nothing; a
denial of a tool whose DANGER does not depend on quoted bytes - code execution,
delegation, a second egress path - is the case exposure exists for.


### 1.24 M13's two-touch model was inert in both halves - a scope correction

M13 is the interaction this architecture is organised around: **approve a
plan, receive a result, nothing in between.** A human touches the task twice -
once to approve what it will do, once when it asks permission for something the
plan did not settle - and code holds it to both. What ran, for the life of the
project until 2026-09-13, was **approve a plan, then get a result regardless.**

**Touch two - the approval - was fully inert.** When a task parked for a human,
the decision was recorded, finalised, re-verified, anchored and logged, and
nothing ever read it. The resumed task started from its goal, re-issued the
call, and parked again under a new approval (SPEC-DELTA C43, fixed in
`f11be4d`). Approving an action never made it happen; denying it never told the
task. Every test of the path asserted that a decision was RECORDED. None
asserted that it had an EFFECT.

**Touch one - the plan pin - was half inert.** Invariant 17 verifies the
approved plan's hash before every step, against the database AND against the
anchor. The anchor half never fired: `checkpoint()` wrote chain heads under the
same key the pin used, so the pin read a chain head and skipped itself (D21,
found 2026-09-07). The database half did work - an edited `approved_hash` was
caught - so an approved plan was protected against accidental change and not
against someone editing the database, which is the case the anchor exists for.

**Stated with the same honesty as 1.21.** M13's two human touch points were
specified, built, tested for recording, and did not constrain execution. The
deputy (M13.3 auto-review) and `approve_scoped_writes` allow within the same
turn and were never affected; they are the paths that actually decided what
ran.

**Whether any published number depended on it - VERIFIED against the record,
not inferred.** In the 283-task measurement database:

| | count | detail |
|---|---|---|
| human decisions (`human.approved` / `human.denied`) | **0** | no human ever decided anything |
| approvals | 3 | all still `pending`; all `run_code`; all 2026-08-24 01:41-01:42 |
| tasks that ever paused | 3 | exactly those three |
| of those, in any evidence file | **0** | searched every file in `docs/evidence/` |
| plans drafted / approved | 1 / 1 | its task appears in no evidence file |
| deputy decisions | 14 | 3 approve, 11 deny - in-turn, unaffected |

No published number came from a run in which an approval or a plan pin
mattered: benchmark runs never waited on a human, and the three that parked
one night in August were abandoned undecided and never reported.

**What holds now:** a decided approval is released on resume through the
ordinary receipt path, at most once, with exposure and the gate's hard denials
re-checked first (invariant 71); the plan pin reads its own namespaced key
(invariant 17, verified by injection in `181159f`).

### 1.25 Code that has never run for real - an inventory (A2-1, A2-2)

A unit test proves a function works and says nothing about whether the system
has ever called it for real (1.21). This is the inventory, taken 2026-09-14
against the measurement record - `agent.local.db`: 283 tasks, 1,214 model
calls, 2026-08-17 to 2026-09-06 - and the evidence files. The eight
`runs/demo-*` databases are scripted-model runs, real harness and no real
model, and count as tests here.

**What the record contains, in full.** Event types: `task.*`, `model.*`,
`tool.intent` / `tool.receipt`, `artifact.written`, `verify.*`, `review.*`
(15 requested, 14 decided), `plan.drafted` / `plan.approved` (1 each),
`skill.loaded` (45), `task.model_stalled` (12), `task.crashed` (3). Tools:
`run_code` 495, `read_file` 113, `list_dir` 5, `write_file` 1. Call purposes:
`step` 1,182, `plan` 1. Nothing else - no integrity, approval, channel,
schedule, session, memory, delegation, web or compaction event of any kind.

#### Paths exercised only against a mock or a fake (A2-1)

| path | exercised today by | to exercise for real | fails silently if wrong |
|---|---|---|---|
| Telegram `HttpTransport`, `serve_forever` | `FakeTransport` | a bot token (D26); Part 4's skip-gated test | update-id offsets against real ids; a 409 when a second poller exists; the 4,096-character limit; callback answers; HTML escaping |
| the Anthropic adapter - all of it; streaming is its only path | a fake client | an API key (D26); Part 4's skip-gated test | cache-token accounting; `stop_reason` mapping; tools-before-system rendering (D33's other half); error and retry mapping; stall detection (D31) |
| the OpenAI-compatible adapter against anything but Ollama | Ollama's `/v1` on 11435 (`phase1-openai-parity.json`) | vLLM, llama.cpp's server, a hosted endpoint | usage fields absent or named differently; stream chunk shapes |
| `web_fetch` / `web_search` over a network | a stub fetcher and searcher; never called in the record | a task that fetches | redirects and final-URL provenance; the byte cap; charsets; a scraper that parses to nothing, which is why `tool.search_failed` exists |
| tier-1 memory: write, consolidate, flush, blessing | tests; 0 memory events in the record | a run with isolation off | the per-user blessing (DR1); consolidation; the flush refusal |
| sessions and boundaries (Phase 9) | tests; 9.6 against real Ollama with scripted decisions, prefill only | a real task that crosses compaction | the seed reference; the snapshot on the session row |
| compaction and the quarantined summarizer | tests; 0 `compress_tool_output` calls in the record | an observation over the threshold, with a local tier | the quarantine prompt; projection |
| delegation | tests; 0 delegation events | a task the five gates admit | the child's narrowed policy |
| the parallel batch | tests; measurement pins width 1 | `max_parallel_tools` above 1 | gate-before-run under threads |
| cron: `supervise`, notifications, the measurement signal | tests | `alpha supervise` against a real model | local-time fields, DST, the grace window |
| releasing a decided approval; the plan pin's anchor half; `integrity.resume` | tests | Part 3 runs each for real | - |
| the skill gate's verdict, `promote`, `decay_pass` | tests; the one real trial run aborted at 10/30 | a completed calibration (D35) | McNemar on real pairs |
| the control plane: server, SSE, console | Starlette's `TestClient` | a browser against a live run | SSE reconnection and backlog; CSRF from a real browser |
| the TUI | tests | a terminal session | - |
| `alpha monitor` | tests; `monitor_flags` has 0 rows | running it over the record | - |
| `alpha snapshot` (the backup API) | tests | copying evidence out of a live WAL database | - |

#### Guarantees, by whether they have ever run (A2-2)

**Exercised in production, with evidence:**

| guarantee | evidence in the record |
|---|---|
| prefix byte-stability (invariant 1) | a `prefix_fp` on 1,214 of 1,214 `model.called` |
| write-before-act, receipts (7) | 614 `tool.intent`, 613 `tool.receipt`; the one without a receipt is a `run_code` whose process died inside the call - its task, `t_9a772f7c`, is still `running` in the record |
| every model call in `calls` (12) | **held only for calls that returned, at call sites that remembered.** 1,183 rows (1,182 step, 1 plan) against 1,214 `model.called`. The 32 that never returned - 17 cut off by a killed process, 12 stalled, 3 crashed - have no row, and neither do the deputy's 15 reviews. **Fixed 2026-09-15 for every call from then on** (D36, C59): the row is written before the call. The record is not backfilled |
| the capability gate | consulted on all 614 tool calls; **its refusal path never fired** - no denial in the record |
| the deputy: a different backend, never widening (14, 15) | 15 `review.requested`, 14 `review.decided` |
| the planner cannot write (13) | 1 `plan.drafted` |
| verification | 427 `verify.attempted`: 73 passed, 354 failed |
| a machine event is never scored (30) | 12 `task.model_stalled`, excluded in `S2c-two-stalls-excluded.jsonl` |
| the measurement invariants (26-29, 33-34, 52, 58) | the ledgers in `docs/evidence/`, including a pass killed at task 5 and resumed from its ledger (RELEASE, G6) |
| the arm's skill set | 45 `skill.loaded` |
| `check_contamination` | 10 real pairs, 2026-08-27 - certified through the empty-set defect Part 1 fixed |

**Exists, never run in production.** `integrity.resume` (9, 53): no task in
the record was ever resumed - none re-entered `running`, and the 15 that
crashed or stalled never ran again. The plan pin's anchor half (17): fixed
2026-09-13, and the one production plan ran under the inert version. The
trusted tier parks (16): no trusted-tier call. Approval release (71): 0 human
decisions, 3 approvals pending since 2026-08-24. Injection blocking (3):
nothing untrusted ever entered a production task, and `artifact.written` in
the record predates the per-write taint field, so taint rebuild (72) has
nothing to rebuild there either. Refinement, compaction, fill-ratio
delegation and quarantine (2, 8, 11, 20). The semantic cache's taint check
(18): one row written, 2026-08-19, and no read recorded. Memory and web
(41-48). Exposure refusals and delegated children (49-51). The parallel
batch (56-57). The stream, the TUI, the banner (59-62). The channel (63-74).
Protected state and every file-route fence (75-77, 83). Sessions (80-82).
Cron (84-88). Part 1's controls (89-96) and Part 2's (97-100). The gate's
verdict, promotion and decay (10). The monitor (6). The integrity alert
channel (40).

**Exists, and cannot run under any current configuration:**

| component | why |
|---|---|
| M7.4 rule 2 - `record_holdout_gap` - and with it the gate's gaming veto | no caller, and a `verify_program` suite has no visible/held-out partition. **Deleted 2026-09-15 with M11 (1.27)**; a promotion now states why the veto does not apply |
| M7.4 rule 3 - `verify_isomorphic` | no caller (D22). **Deleted 2026-09-15 with M11 (1.27)** |
| `measure_retrieval_precision` | no caller. **Deleted 2026-09-15**: no skill has ever been promoted, so it measured 0.0 by construction (C61) |
| local-tier routing, `local_with_escalation` | `Router.choose` raises unless `_ESCALATION_WIRED`; one tier is configured |
| `tokens.calibrate` | no caller; no `.token_calibration.json` exists. **Deleted 2026-09-15**: it needed an API key and calibrated to the wrong tokenizer. Recorded usage is the server's own count; the in-loop estimator stays uncalibrated (D6) |
| `max_taint_in` | carried on every contract, consulted nowhere. **Decided 2026-09-15: DELETE.** No check could give it a meaning (C60). **Dead in code the same day**: its one reader is gone and no other value can be set. Its bytes stay in the prefix until the next HARNESS_VERSION bump (D38, invariant 109) |
| `generate_skill`, and invariants 4 and 5, which only it reaches | no entry point - and since Part 2, refused in code until M7.4 rules 2 and 3 run. **Retired unanswered 2026-09-15, with invariants 4, 5 and 100 (1.27)** |
| POSIX rlimits | only Windows has ever run this. **Stated 2026-09-15** in README and RELEASE: on Windows the sandbox was advisory path confinement plus a timeout. **Built the same day (D37):** a Job Object enforces memory, CPU time and process count on Windows; file size waits for Phase 6. The POSIX path itself has still never run |
| the live Telegram path; the Anthropic adapter | no token, no key (D26) |

**One thing the record cannot say, and now will.** What the recorded programs
did: each of the 495 keeps an 80-character repr in its chained intent. From
Part 2 on, `run_code` records its full source, the source's hash on the chain,
and every path the program touched (A2-4). The 495 are not recoverable.

**Part 3, 2026-09-14: three rows moved - run for real, with evidence.**
The release of a decided approval, the plan pin's anchor half, and
`integrity.resume` each ran in a real process, and the event log now says
so, because each records what it verified (invariants 101-103).

Two real runs, granite4.1:3b on the dedicated Ollama, through the real CLI,
every step its own process, on their own database - never `agent.local.db`:
plan, approve (the pin goes to the anchor), run (parks on a `write_file`),
`approvals --decide` in a separate process, run again in a new one.

| | run A - `part3-real-runs.json` | run B - `part3-real-runs-2.json` |
|---|---|---|
| task | `t_fc9a3b2505ec` | `t_0f12b3909a36` |
| outcome | done, `work/hello.txt` = 'hello', chain verifies: True | done, `work/hello.txt` = 'hello', chain verifies: True |
| `integrity.verified`, first run / resume | anchor `none` / **`matched`** | anchor `none` / **`matched`** |
| `plan.pin_verified` | 2, every one `matched` against the anchor: True | 4, every one `matched`: True |
| decided approval released on resume | a_bdf9e58e2abd - executed | a_7c6bd60da2e3 - executed |
| pin verified before the release, in the resumed process | **no** | **yes** |
| every command exited 0 | False - the resumed `alpha run` exited 1 | True |
| wall clock, all commands | 137 s | 114 s |

Run A found two defects no test had: the resumed process released the
decided write BEFORE it verified the pin (invariant 17 now covers a release),
and `alpha run` crashed printing the cache ratio every local backend leaves
unmeasured - after the result, on a finished task. Run B is the same task
shape on the fixed code. This does not change the measurement record's
count: no task in `agent.local.db` was ever resumed.

### 1.26 The deputy was off the record - invariant 12 false twice, in two different ways

Invariant 12 says every model call is recorded in `calls`. Fixing D36 found it
false in two distinct ways, and the second is the finding.

**1. Calls that never returned.** The row was written from the returned
response, so the 32 calls in the record that never returned have no row: 12
stalled, 3 crashed, 17 cut off by a killed process. This is the gap D36 was
opened for. Its timing consequence turned out to be small: per-call median
53.3 to 53.4 s, and no headline figure moves (CHANGELOG 2026-09-15).

**2. Call sites that never wrote a row at all.** Five places called their
backend directly: the deputy, the quarantined summarizer, the monitor, the
self-consistency verifier and the generator. Four never ran in production. The
deputy did: **15 reviews in the record, 23.5-167.7 s each, and not one row in
`calls`.**

**Why the second one matters more.** The deputy is a security control. It
decides whether an action runs (invariants 14-16). A control whose own model
calls leave no telemetry has a cost, latency and failure rate that no
calls-derived figure can see. Its 15 decisions were visible as `review.*`
events. The seconds and tokens it spent to reach them were invisible, and they
are missing from the token columns of four published evidence rows.

**How it hid.** `test_no_call_bypasses_telemetry` compared `calls` rows with a
spy that summed every backend's calls. That check was sound, but its scenario
was `list_dir` then `<final>`, which never reaches a review, a quarantine, a
monitor or a verifier. A guarantee stated over *every call* was tested over
*one call site*. The shape is 1.21's: a unit test proves a path works and says
nothing about which paths exist.

**Fixed 2026-09-15 (D36, SPEC-DELTA C59).** There is now one door,
`AgentContext.call_model`, which writes the row before the call. A structural
test fails on any `.complete(` outside it, and the deputy has a behavioural
test of its own (invariants 104-106). The record is not backfilled.

### 1.27 The original research question is retired unanswered

**The question** - do self-generated skills help? - was the reason this
project exists (RELEASE, G4). It was never answered, because the generator
never ran. **On 2026-09-15 it was retired, unanswered.** That is recorded here
as a finding, not tidied away as a cleanup.

**What was retired.**
- M11 skill generation: `generate_skill` and its grounding, drafting and
  test-authoring pipeline.
- M7.4 rule 2: `record_holdout_gap`, the visible/held-out gap, and with it
  the gate's gaming veto.
- M7.4 rule 3: `verify_isomorphic`.
- Rule 1 (`assert_different_authors`), the held-out registry, and the skill
  store's visible/held-out test storage. These existed only to serve the
  generator.
- Invariants 4, 5 and 100, withdrawn.

**Why retired rather than kept gated.**
1. **Its own baseline says it does not work.** The published baseline this
   project adopted for self-generated skills is **-1.3 pp** (3.4, HANDOVER
   section 13). The literature puts curated skills around +16 and generated
   ones slightly negative.
2. **The suite that would evaluate it is archived.** Its failures were
   unstructured (1.13), so no skill could produce the discordant pairs the
   gate needs.
3. **D19 is unclosed.** A generated skill's provenance cannot tell "no
   sources existed" from "retrieval broke".
4. **None of its three components ever ran.**

Keeping M11 meant carrying three gated items to justify a milestone whose
baseline says it does not work.

**Removing the veto removed no protection.** The veto read a gap that nothing
ever recorded, so it could not fire. For a hand-written skill the gap is
undefined, not unmeasured. Every check is held out, because the grader is
unreachable (SPEC-DELTA C1). Every promotion now states that in its
`gaming_check` (invariant 110).

**What would reopen it. All three are needed:**
1. **A live suite** whose failures are structured enough for a skill to move,
   one that passes the 1.14 pre-test. `aero_calc` is archived and does not
   pass it.
2. **D19 closed.** Provenance must distinguish "no sources" from "retrieval
   broke" before a generated skill's grounding means anything.
3. **Both rules wired, not gated:**
   - the visible/held-out gap recorded from a generated skill's own checks, and
     read by the gate;
   - isomorphic verification with a chosen second verifier per task, its model
     calls attributed to the task (D36).

**How to pick it up.** The code as it stood is at commit `ce624c7`, the parent
of the retirement:
- `git show ce624c7:src/agent/skills/generate.py`
- `src/agent/assure/mesh.py` (rules 1-4, `HoldoutRegistry`, `partition_tests`)
- the veto branch in `src/agent/skills/gate.py`
- `tests/test_m11_generator.py` and `tests/test_gated_generation.py`

`promotion_rate` survives, moved into `skills/gate.py`: the gate is what
promotes, so the gate reports the rate.

`AgentContext.research_retrieve`, the grounding step, left with the generator
too. The console's Grounding notes are still stored, and
`userdata.grounding_sources` still answers a query, but **nothing reads them
now**. This is a method with no production reader, the class of thing the
dead-code audit cannot see (D22). It is recorded here so that reopening M11
finds its input path intact.

### 1.28 An entry point is the least-tested code in a project

**The rule.** A test imports what an entry point CALLS, instead of calling the
entry point. Coverage accumulates underneath the front door and never touches
it. The deeper the unit coverage, the more convincing the illusion: 769
passing tests are evidence about functions, not about the program.

**Measured 2026-09-16: two documented front doors were dead for ten days,
both broken by ONE commit** - `b0bdcd7`, 2026-09-06, "Corrections: the
prefix-cache alarm can fire, and the class-name defect is swept" - with the
full suite green over both.

| front door | documented in | how it died |
|---|---|---|
| `bench/noise.py main` | README, step 1 of the measurement flow | `exec_cfg = ctx.config.backends["cloud"]` was added forty-five lines ABOVE the `ctx = open_measurement(...)` that defines it. `UnboundLocalError` before the first task (C64) |
| `demo.py` | README; HANDOVER section 2, step 4; RELEASE's one end-to-end demonstration | it did all the work, then formatted an unmeasured cache ratio with `:.0%` on its last line. `TypeError`, exit 1 |

`demo.py` was bisected: it worked at `7aed79f` and failed at `b0bdcd7`, which
introduced the `measurable` guard that makes the ratio None for a local
backend. **It is C58's defect** - the same `:.0%` on the same None - found in
the CLI on 2026-09-14 by a real run, fixed there, and never swept for
siblings. The second site sat in the demo everyone is told to run first.

**Both were found the same way:** by writing a test that calls the entry
point rather than its parts.

#### The sweep: every entry point, and what invokes it

"Starts" means `--help`, which runs the module's imports and builds its
parser - where a missing import or a mis-wired subcommand shows. It is not a
claim that the command works.

| entry point | documented | invoked by a test BEFORE 2026-09-16 | now |
|---|---|---|---|
| `demo.py` | README, HANDOVER, RELEASE | **no** - dead 10 days | run in full, output asserted |
| `bench/noise.py main` | README step 1 | **no** - dead 10 days; a test added 2026-09-15 after the outage | to its first decision, plus starts |
| `src/agent/cli.py main` (`alpha`) | README, HANDOVER | **no** - two private helpers were imported, the front door never called | starts |
| `bench/run.py main` | README step 2 | **no** - `cmd_paired` is called directly by 11 tests, `main` by none | starts, plus `cmd_paired` |
| `bench/report.py main` | README step 4 | **no** - its render functions are tested | starts |
| `bench/coverage.py main` | HANDOVER | **no** - `load_tasks` is tested | starts |
| `bench/ladder.py main` | HANDOVER, FINDINGS 1.6-1.8 | **no** - source-text scans only | starts |
| `bench/nextstep.py main` | HANDOVER | **no** - its internals are tested | starts |
| `bench/cache_boundary.py main` | FINDINGS 1.18 | **no** | starts |
| `bench/suites/archive/aero_calc.py main` | README (suite loading) | **no** - the suite's data is tested | starts |
| `bench/archive/decompose_aero.py main` | FINDINGS 1.14 | **no** - the decomposer is tested | starts |
| `agent.control.asgi:app` (uvicorn) | README | **yes** - `test_m12_serve` spawns the real server | unchanged |
| `bench/cache_reuse_probe.py` | FINDINGS 1.18 | **no** | **still none**: no parser, and it needs a live model server |
| `alpha.ps1`, `setup.ps1`, `ollama-serve.ps1`, `measure.ps1` | README, HANDOVER | **no** | **still none**: no test runs PowerShell |

**Before today, 1 of 13 program entry points had a test that invoked it.**
Two more now do in full, nine more at least start, and the remaining two are
named above with the reason.

**The next layer down is the same risk, unclosed.** The CLI's **25
subcommands** are reachable now only through `--help`. Part 3 ran five of them
for real, by hand, and that run found two defects no test had (C58). Nothing
automated enters those rooms.

#### The same failure, one level down, found the same session by another route

`fetch_url` (see 1.27's deletions) was a wrapper that no production path
called, and **two network-safety tests were guarding it** - non-http schemes
and private addresses - while `web_fetch` ran `fetch_with_provenance`. The
tests were retargeted at the function that runs.

The two finds are one failure at different distances from the front door:
- a test can guard code that nobody calls (`fetch_url`);
- no test guarded code that everybody calls (`main`).

Both were invisible to the dead-code audit, which reads production call sites:
`fetch_url` had one until its caller was deleted, and `main` is called by the
operating system.

---

## 8. Open research questions

Recorded so they are not re-derived, and kept separate from findings because
nothing here has been measured.

### 8.1 Does prefix CONTENT move pass rates, or only prefix STRUCTURE?

The harness-tax result (1.6) shows that scaffolding structure moves pass
rates: R0 through R5 differ in how the task is framed, and the spread was
eight points. What it does not separate is whether the *content* carried in
the prefix matters comparably.

The question became concrete in Phase 3. A benchmark run gets an empty
tier-1 memory block while a real session at week ten carries a populated one,
and both have the same prefix SHAPE. If content matters as much as structure,
the two are not the same instrument, and every measured number is a number
about the empty-memory case only.

**The experiment:** the same task, twice, identical in every respect except
that one arm's tier 1 holds plausible accumulated facts irrelevant to the
task and the other's is empty. If pass rates differ, prefix content is a
measurement variable and every published number needs the memory state
attached to it, the way the profile already is.

**Why it is worth doing rather than assuming:** the cheap assumption is that
irrelevant context is harmless. This project has already found one case where
the opposite held — the harness tax was 8pp of pure framing — and one where
the intuitive answer was backwards (answer position, not response length,
1.6). It is also cheap: two arms, one suite, no new machinery, and the
memory state is already recorded per run.

**Blocked on:** nothing technical. It is a Phase 9 candidate because
compression is when tier 1 is rebuilt and the boundary is already being
instrumented.

### 8.2 Is `--profile local` measuring a different question than the spec asks?

Adjacent to 8.1 and recorded for the same reason. The measured system now
differs from the shipped one in at least three named ways: `auto_review` is
off, tier-1 memory is empty, and retrieval is scoped to the attempt. Each is
individually justified and each is recorded in the run record. What has never
been measured is whether they interact — whether a system with all three
disabled is meaningfully easier or harder than the one a user runs.

