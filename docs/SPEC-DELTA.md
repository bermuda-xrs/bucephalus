# Spec delta

Everywhere `spec/BUILD-SPEC.md` and `spec/EXPLAINER.md` are now known to be
wrong, with the correct version. The originals are untouched; merge from here.

Three sources: the author's own retractions, the ten already in
`spec/CORRECTIONS.md`, and everything learned by running the system.

Each entry says whether the divergence was **deliberate**, an **oversight**, or
a **workaround**, because that determines whether the spec or the code should
move.

---

## A. Author retractions (spec is wrong, code is right)

### A1. `cache_hit_ratio` is not a usable invariant on a local profile
**Spec:** §M2/M3 make it a monitored invariant with an acceptance test
(`test_cache_hit_ratio_above_threshold`) and an alarm.
**Reality:** it is a cloud-billing metric. `cache_read_tokens` is populated
**only by the mock backend**; across 566 real local calls it is exactly 0. The
acceptance test passes because `mock.py` fabricates a hit ("Pretend the prefix
cached after the first call").
**Correct version:** report `n/a` under `--profile local`, never 0.0, and
drive the assertion from `PrefixGuard` fingerprint stability — which *is*
enforced and *is* meaningful locally. Invariant 1 (byte-identical prefix)
stands; what is unverifiable is whether it buys anything, because Ollama has
no prompt-cache accounting.
**Class:** oversight — a cloud-shaped metric applied to a local-only path.

### A2. The difficulty tiering is not a property of the suite
**Spec:** bench suite doc — "Easy ~80%, Medium ~50%, Hard ~20%, mean ~50%".
**Reality:** written for an unspecified model. The sensitivity band is a
property of **suite × executor**. On `granite4.1:3b` Q4 on CPU, the observed
mean was 10%.
**Correct version:** state the executor with any target band, and treat the
band as a joint property. A band quoted without an executor is meaningless.
**Class:** oversight.

### A3. The scorer is a step function where it matters most
**Spec:** `max(0.0, 1 - rel/TOL)`.
**Reality:** every answer outside tolerance scores exactly 0.0, and 14 of 15
wrong-number tasks live there. The M7 ratchet therefore cannot distinguish a
3% miss from a 300% miss, and `plateau` fires after three identical zeros. Of
30 tasks in pass 1, exactly **one** ever moved its score.
**Correct version:** `1 - 0.5·rel/TOL` inside the band, `0.5/(1 + log10(rel/TOL))`
outside — monotonic, continuous at the boundary, asymptotic to 0.
**Critical:** `passed` must be computed from the tolerance **independently**.
The old `passed = all(s > 0.0)` combined with an asymptotic score makes
everything pass.
**Class:** oversight, now fixed in code.

---

## B. Already in CORRECTIONS.md (§1.1–1.11, §2.1–2.7)

Unchanged and still correct: `muse-glimmer:3b` does not exist; `tiktoken` is
the wrong tokenizer; `temperature` now 400s; the semantic-cache taint
comparison was inverted; `agreement >= 0.67` never accepts 2 of 3; the
exact-string regex missed relative paths; the shipped config violated
invariant 14; the M5.3 sandbox could not pass its own M5 test; the schema
omitted M12/M13 tables; `context_fill_ratio` against a 1M window is
meaningless.

Plus the incomplete set: Ollama's silent 4096 truncation, cold-load cost,
small models needing constrained decoding, `$0.00` budgets, the throughput
probe breaking invariant 12, Textual over Ink, and "testing a function is not
testing that the system calls it".

---

## C. Learned by running it

### C1. The grader was inside the agent's reachable workspace
**Spec:** invariant 5, "held-out tests never enter any prompt."
**Reality:** the suite wrote `work/<key>/verify.py` — containing
`EXPECTED = {...}` in plaintext — and `artifact_scope` was `["work/<key>"]`.
The answer key sat in the agent's own directory. The invariant was satisfied
in letter (nothing entered a *prompt*) and violated in substance.
**Correct version:** grading runs from a directory outside every path in
`artifact_scope` (`grading/<key>`), and only the grader's *text* is carried
into the sandbox. Add the assertion: no file inside `artifact_scope` may
contain an expected value or the string `EXPECTED`.
**Class:** oversight in the suite, which the spec never specified (it says only
"your own 30-task suite"). Worth adding to the spec as a requirement.

### C2. M7.4 rule 4 (anti-gaming veto) cannot fire for a `verify_program` suite
**Spec:** M7.4 lists the visible/held-out gap as a standing defence.
**Reality:** `record_holdout_gap` has no callers. The only visible/held-out
partition is in the M11 generator (`holdout_frac=0.30`), which has never run.
A `verify_program` suite has one grader per task and no partition, so the gap
is uncomputable and `_holdout_gap` always returns `None`.
**Correct version:** mark the veto **profile- and suite-conditional**. Every
promotion must state whether it ran; a promotion that silently skipped the
check is indistinguishable from one that passed it.
**Class:** oversight. Most dangerous in combination with C1.

### C3. M6.3 escalation-by-disagreement has no call site
**Spec:** M6.3 specifies escalation on self-consistency disagreement.
**Reality:** `local_with_escalation` is called only by tests. Under the
shipped single-tier config it is also structurally unreachable — `choose()`
returns `backends["cloud"]` on its first branch because `local_allowlist` is
empty and `active_local_tier` is null.
**Correct version:** mark the invariant profile-conditional, and require that
configuring a second tier surfaces the gap. **The test fixture has configured
two tiers all along**, so escalation should have been exercised by the suite
and never was.
**Class:** oversight.

### C4. The measured system is not the shipped system
**Spec:** M13 makes the deputy and two-touch autonomy central.
**Reality:** every benchmark run sets `auto_review = False`, and the shipped
`config.local.yaml` maps the **cloud** slot to `granite4.1:3b`. So there is
one tier, no routing decision is ever made, no escalation is possible, and the
deputy — the component that makes unattended operation work — is off in
exactly the runs that produce the numbers.
**Correct version:** either measure with the deputy on, or state in the report
header that the measured system differs and name every disabled component.
Silence is not acceptable. Part 13's Pareto chart has **no cost axis** under
an all-local profile because every call costs $0.0000.
**Class:** deliberate for the deputy (an LLM in the decision path is variance
in the signal being measured), oversight for the single tier.

### C5. The cost axis must be seconds, not dollars
**Spec:** Part 13 — "x = mean USD/task".
**Reality:** zero on an all-local rig, so the chart collapses to a line.
**Correct version:** x = median wall-clock **seconds** per task; keep the
dollar column populated with zeros and labelled "n/a under local profile".
The spec's own laptop-tier reasoning already says output tokens are the budget
on constrained hardware — this is the same argument.
**Class:** oversight; the spec assumed a paid tier existed.

### C6. `Budget.spent_seconds` measured model time, not elapsed time
**Reality:** it accumulated `resp.wall_ms` only, so sandbox execution and
verification were free, and `budget_exhausted` became contention-sensitive —
two processes on one CPU could manufacture that outcome on a task that would
otherwise finish. Observed: H24 flipped outcome under contention.
**Correct version:** charge wall clock. A budget is a deadline.
**Class:** oversight.

### C7. `max_steps: 6` was a step budget masquerading as difficulty
**Reality:** 11 of 30 tasks never reached a single verification. Six steps
must cover reading inputs, writing a program, recovering from a syntax error,
and emitting a final.
**Correct version:** 10, and report `ended_at_step_limit` so the distinction
between "hard" and "ran out of steps" is visible.
**Class:** oversight.

### C8. Constrained decoding on the action envelope creates a new failure
**Spec (via CORRECTIONS §2.3):** use Ollama's `format` for the action protocol.
**Reality:** it guarantees the JSON *envelope* is valid and says nothing about
the string inside it. The model closed the object from within the `code`
field — `print('Answer written to', output_path)}` — byte-identical across
unrelated tasks, in 10 of 30 tasks and again in 2 of 5 after a compile-check
landed. Each occurrence costs a step (~44s median).
**Correct version:** use the tag protocol `<code>…</code>` for code actions.
Python inside a JSON string is the problem; a tag has no escaping. Keep a
compile-check as defence in depth, and keep accepting JSON so the change is
strictly more permissive.
**Class:** workaround that became a defect.

### C9. `verify_program` resolved paths against the process CWD
**Reality:** run from anywhere but the project root, it reported "no verify.py
found" on **every** task — a silent 0% indistinguishable from a suite that is
too hard. It also made tests whose workspace was under `tmp_path` pass
*vacuously*.
**Correct version:** resolve against `config.root`, passed by the mesh.
**Class:** oversight.

### C10. The task contract left the I/O shape to inference
**Reality:** the objective documented the `constants.json` layout in detail
but named only the *path* of `params.json`. The agent guessed key names —
`mass_flow_rate` for `mdot_kg_s`, `thrust` for `thrust_n`, `Isp` for `isp_s`,
`dry_mass` for `dry_mass_kg`. 8 of 30 tasks died on a `KeyError` before any
physics.
**Correct version:** name every input and output key verbatim. A benchmark
that measures name-guessing is not measuring what it claims.
**Class:** oversight.

### C11. Nothing prevented two measurement processes at once
**Reality:** two passes overlapped for 36 minutes. Three tasks were measured
under 2× CPU load; since `budget_exhausted` is a time limit, contention can
*manufacture* that outcome. The single-process invariant existed in prose
only — the hash-chain lock is process-wide and a second process cannot see it.
**Correct version:** an OS-level advisory lock, released by the kernel on
process death (these processes are routinely killed, not exited).
**Class:** oversight.

### C12. The workspace was never reset between runs or arms
**Reality:** `provision()` only *wrote* fixtures; `output/answer.json` from a
previous run survived. `verify_program` grades files, so a run producing no
answer was graded against the previous run's answer. In a paired trial the two
arms share one workspace, so the **control arm could be graded against the
answer the skill arm just wrote** — manufacturing the exact lift the gate
exists to detect.
**Correct version:** delete and rebuild each scope directory before every arm
of every run.
**Class:** oversight. The most dangerous one found.

### C13. A venv `python.exe` on Windows is a trampoline
**Reality:** `Popen` returns the wrapper's pid while the real interpreter runs
as its child. `proc.kill()` leaves the interpreter alive. This made
`test_kill_and_resume`'s claim of "a real kill, no `finally` runs" false — it
passed on the marker file regardless.
**Correct version:** kill the process tree, or have the child report
`os.getpid()`.
**Class:** oversight, environment-specific.

### C14. Refinement receives a scalar, not an observation
**Reality:** after each `<final>` the loop appends "Verification failed
(verify_program, score 0.00)". That satisfies invariant 2 structurally — it
*is* an external observation — but it names no key, no direction, and no
magnitude. Combined with A3's step function, the agent is told "wrong" three
times identically and plateaus.
**Correct version:** either enrich the observation (which key, which
direction) or state plainly that M7's refinement loop is not exercised by a
suite whose grader returns a scalar. **A3's gradient fixes the ratchet's
ordering; it does not fix this.** Both statements belong in the report.
**Class:** oversight in suite design, not in M7.

### C15. Aerospace physics may be the wrong task family for a 3B
**Spec (EXPLAINER):** the local tier's scope is "summarizing, sorting,
extracting", earning more responsibility only when measurements justify it.
**Reality:** the aero suite asks for multi-step numerical reasoning *and*
program synthesis *and* file I/O. Bucket D was 14/15 unstructured errors with
a median relative error of 100%, and five tasks returned an input constant
verbatim — a program that ran and never implemented the physics.
**But:** D1 showed the same model answers E01 correctly, 5/5, when asked
directly. So the family may be fine and the *task shape* wrong — E01's goal is
115 words of which 10 are physics and 105 are I/O contract.
**Correct version:** either match the family to the spec's own stated scope,
or reduce the plumbing so the physics is the task. Decide on evidence, not
assumption.
**Class:** spec self-inconsistency — the local-tier scope and the benchmark
design contradict each other.
