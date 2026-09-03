# Findings

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
* **M7.4's anti-gaming veto has never fired.** `record_holdout_gap` has no
  callers and a `verify_program` suite has no visible/held-out partition, so
  every promotion carries `gaming_check: "NOT MEASURED"`.

---

## 3. Pending

* **3.1 Noise floor, both arms** — *pending Phase 4.*
* **3.2 Calibration** — *pending Phase 5.* Reported with the contamination
  caveat in the same paragraph: the skill's author saw the whole suite, so no
  split is clean with respect to the author. It is a **positive control on the
  detector**, not a claim of generalization.
* **3.3 Module ablation** — *pending Phase 5.3.* The highest-value single
  measurement here. If lift collapses without module 3 (the magnitude check),
  the skill works by supplying the external observation the bare agent lacks —
  a mechanism, not a score.
* **3.4 M11 generated skills** — *pending Phase 6.* The published baseline is
  **−1.3pp** for self-generated skills, so **0 of 3 promoted is the expected
  result and a valid finding.** It will not be tuned into a pass.

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
