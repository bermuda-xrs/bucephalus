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
