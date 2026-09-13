# Spec delta

> **The aerospace suite is archived.** As of 2026-09-05 `aero_calc` lives
> under `bench/suites/archive/`, `bench/tasks/archive/` and
> `bench/archive/decompose_aero.py`, and is no longer any tool's default —
> `--suite` and `--tasks` are now required arguments. Every result below
> remains reproducible from the archive. Nothing was deleted. See FINDINGS
> 1.13 for why it was retired.

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

### C16. The ladder's R4 rung scores step allocation, not correctness
**Instrument:** `bench/ladder.py`, rung R4, documented as "the full agent
loop, one step allowed" and read as answering "can it deliver this".
**Reality:** R4 sets `max_steps=1`, so it actually scores *whether the model
spent its single action on `run_code`*. Measured across five samples: one
spent its step on `run_code` and produced an answer; four spent it on
`read_file(params.json)` — correct agent behaviour — and produced no artifact
at all, in 7.2–7.5 s.
**Why it matters beyond one rung:** the bias is directional. "Work through it
step by step" is an instruction to take preparatory steps, so R4 systematically
penalises the intervention it was being used to test. The published v2 R4 row
(0/5, answer 11185784.5) carries the same defect and must not be read as
evidence that the harness tax survives into the full loop.
**Correct version:** either give R4 enough steps to read-then-compute (2–3),
or rename it and report it as what it measures — a one-action budget probe —
and treat R5 as the only loop rung that speaks to correctness.
**Class:** oversight. Fourth in the instrument class, found by applying the
documented test: what would this check score for an agent that did the work
perfectly but behaved slightly unusually?

### C17. `reap_orphans()` kills healthy workers, not just orphans
**Spec/House rule:** "route every termination through `kill_tree()`, and reap
orphans before any timed run" — the guard against the documented 5.9 GB
orphaned `llama-server`.
**Reality:** `ORPHAN_NAMES = ("llama-server",)` and the reaper matches on
**process name alone**, then `kill_tree()`s every match. It cannot distinguish
a live child of a running Ollama from a true orphan. Observed killing the warm
model three times during Phase 1, between ladder runs, defeating
`keep_alive: 30m`.
**Consequence for published numbers:** R0 always runs first in a ladder, so
R0's first sample always absorbs a cold model load (~58 s on this hardware).
That inflates the R0→R1 **wall-clock** drop cited as evidence of suppression.
The effect survives on warm samples (E04: 24.3 s → 12.0 s), so the finding
stands, but `45.7 s → 7.7 s` is partly cold-load and is not a clean effect
size. Blast radius is limited: `noise.py` calls the reaper once in preflight,
not per task, so a full-suite median is unaffected — only the first task.
**Correct version:** reap only processes whose parent is gone (or which are
not children of the live server), not every process with a matching name.
**Class:** oversight — and the **mirror image** of the documented class-2
pattern. Class 2 is "anything spawned outlives what spawned it"; this is "the
reaper cannot tell a live child from an orphan". Fourth in the process class.

### C18. Temperature 0 is not byte-deterministic on this stack
**Assumed everywhere:** that warm-cache runs at temperature 0 are
byte-identical, so within-process variance is near zero and repeat passes add
nothing.
**Reality:** four counterexamples on identical prompt bytes, across both
models —

| observation | first sample | subsequent samples |
|---|---|---|
| E05 R1 (3B) | 362 ch, **wrong** (8775.75) | 685 ch, **right** (12918.75) |
| E04 R0 (3B) | 537 ch | 561 ch |
| E01 R1 (8B) | 7674.6, 1097 ch, 127.1 s | 7663.2, 1002 ch, ~78 s |
| E01 R5 (3B) | 7909.54 | 250.12, 250.12 |

The pattern is specific: **the first sample of a block diverges; subsequent
samples are byte-identical to each other.** Divergence is tied to the first
call after a model load or a rung change. `ladder.py` rebuilds the prompt
identically per sample and `complete()` sends an identical body, so this is
not a prompt artifact.
**Why it matters:** E05 is a *correctness* flip, not a cosmetic one — the same
task at the same rung with the same bytes both passed and failed. `n=1` per
task inherits this, and the first task of any run is the most exposed.
**Correct version:** do not justify cutting repeat passes on determinism
grounds. If a variance estimate is wanted, use **cold-start** repeats, because
cold start is the condition that produces the divergence.
**Class:** oversight — an assumption about the backend that was never tested.

### C19. The harness tax is answer position, and it does not generalize
**Earlier version (HANDOVER §6):** "the mechanism: chain-of-thought
suppression", evidenced by response length collapsing 1155 → 236 → 32
characters.
**Reality:** length is a **correlate**. The discriminator is where the answer
appears — failing responses emit `key: value` before any working, passing
responses emit the number last. E04's R1 failure states `delta_v_m_s: 8860`,
then writes the *correct* formula and the *correct* substitution and never
evaluates them: post-hoc rationalisation anchored to a token already in the
context, which autoregression cannot revise. A response that commits to a
number first has nothing left to write, which is *why* it is shorter.
**The control:** E05, same task, same rung, same prompt bytes, temperature 0,
produced both behaviours — the answer-first sample wrong, the two answer-last
samples right. That isolates position from task, rung and prompt in one
comparison; the length story cannot.
**Scope correction:** the tax reproduces on **1 of 3** tasks (E04; E05
partially; E03 not at all), and the 8B is **unaffected** (5/5 at R1 where the
3B is 0/5). It is neither general across tasks nor independent of capacity.
**Consequence:** the intervention to aim for is not "make the model reason
more" but "stop it emitting the answer token first". The v3 step-by-step
instruction was the former, and it failed at R5 (0/5) — while improving error
*structure* 3.6× on the monotonic scorer, which is a real effect and not a
pass.
**Class:** oversight in the original inference — a correlate read as a cause
from a single task.

### C20. `granite4.1:8b` does not fit this machine alongside normal desktop use
**Spec (§0.1b) and HANDOVER:** the 8B is the desktop/heavier local tier.
**Reality, measured:** at the executor's `usable_context_window: 16384` the
8B is **7.64 GB resident**. Free memory with no model loaded has been observed
between **3.6 and 6.4 GB** depending on what else is running, so loading it
leaves **0.79 GB or less** — against a house-rule floor of 2 GB free. This is
not an orphan and cannot be reaped; it is the OS, the editor, the browser and
any second model runtime.
**Evidence it bites:** one model call in Phase 1 took **2465 s** against a
600 s budget and manufactured a `budget_exhausted` — 29× the median for that
rung. Since `budget_exhausted` is a *time* limit, memory pressure converts
into apparent task difficulty.
**Correct version:** state the free-memory precondition (~8.5 GB) as part of
the 8B run procedure, not as a general "check free memory" note, and record
free memory per task rather than only at preflight.
**Class:** oversight — the hardware note records total RAM but never the
headroom an 8B run actually needs.

### C21. The suite was the default, which is how it became load-bearing

**Reality:** `noise.py --suite` defaulted to `aero_calc`, `coverage.py
--tasks` to `bench/tasks/aero_calc`, and `ladder.py` hard-coded
`get_suite_by_name("aero_calc")` for its loop rungs while carrying four tasks'
orbital mechanics as source literals. No one ever chose the aerospace suite
for a given run; it arrived by default and stayed.

**Correct version:** a suite is a REQUIRED argument everywhere, with no
fallback. A missing suite fails loudly naming what is missing. `ladder.py`
takes its prompts as suite-supplied data and raises on an unknown task rather
than silently substituting E01's physics — which would measure a different
question and report it as the right one.

**Class:** oversight, and a slow one. Convenience defaults are how a general
instrument acquires a domain.

### C22. Sandbox libraries are a second extension surface and were unmanaged

**Reality:** anything installed in the environment was importable by
model-written code inside `run_code`. Tools pass the capability gate;
imports do not. So the set of things a model could reach was whatever
`setup.ps1` happened to install, undeclared and unversioned.

**Correct version:** `execute/libraries.py`. Each library declares a pinned
version and explicitly whether it touches network, writes outside
`artifact_scope`, or spawns subprocesses. Registration REFUSES anything
answering yes. `TOOLSET_VERSION` is stamped on every run and `report.py`
refuses to plot across differing values — because a model that can import
sympy is not attempting the same problem as one that cannot, even though the
pass criterion is unchanged.

**Class:** oversight. The gap was never exploited; it was simply never named.

### C23. An instruction can carry the same defect as an instrument

**Reality:** five instruction-level errors in this iteration had the identical
shape as the code defects — a stated criterion measuring something adjacent to
what it claimed. An abort threshold counting "experienced a machine event"
when the thing that mattered was "lost data". An ablation design measuring
necessity-given-the-others when the question was sufficiency. A premise that a
value was "unstructured" when it decomposed exactly.

**Correct version:** apply the same test to instructions as to checks — *what
would this score for something that behaved correctly but unusually?* — and
decompose values rather than inheriting labels.

**Class:** not a spec error. Recorded because the taxonomy was incomplete
without it, and because it was caught by the same discipline as the rest.

### C24. The wheel never shipped the operator console

**Reality:** `pyproject.toml`'s `package-data` listed `themes/*.toml`,
`assets/*.ans` and `py.typed`, but not `control/*.html`. `ui.html` is 569
lines and is read at request time by `GET /`, so a pip-installed control
plane started, served its whole JSON API correctly, and raised
`FileNotFoundError` on the one route a human opens.

**How it was found:** verifying that Phase Q had not broken the user-facing
paths. It had not — this predates the quarantine entirely and was never a Q
regression. It survived because every test and every launcher runs from the
checkout, where `ui.html` sits beside `server.py` and the packaging is never
exercised. `scripts/postinstall_check.py` installs the wheel and runs a task,
which is why it passed: it never opens the console.

**Correct version:** `control/*.html` added to `package-data`. Verified by
rebuilding and reinstalling into a clean venv in another directory:
`ui.html present in installed copy: True - 26287 bytes`.

**Class:** oversight, and the same shape as the rest — a check that measured
something adjacent to what it claimed. The postinstall check verifies "an
installed wheel can run a task", which is true and was never the same claim
as "an installed wheel is usable".

### C25. `wmic` is not available on current Windows builds

**Reality:** `wmic.exe` is deprecated and absent on this Windows 11 build.
Anything shelling out to it fails with `FileNotFoundError [WinError 2]`.
Nothing in `src/` depends on it — this was hit in an ad-hoc diagnostic — but
it is worth recording before someone reaches for it.

**Correct version:** `Get-CimInstance Win32_Process` from PowerShell, or
`proc.process_table()`, which already reads parentage through
`CreateToolhelp32Snapshot` and needs no subprocess at all.

**Class:** environment drift.

### C26. The sandbox cannot survive a PyInstaller freeze — it becomes a fork bomb

**Spec (M13.5 / the Tauri desktop plan):** ship the control plane as a frozen
sidecar so the app needs no Python on the target machine.

**Reality:** `execute/sandbox.py` spawns its child with

    argv = [sys.executable, "-I", "-u", str(script)]

Under PyInstaller `sys.executable` is **the frozen application**, not an
interpreter, and the bootloader passes `-I -u script.py` through as plain
`sys.argv` rather than honouring them as interpreter flags. So every
`run_sandboxed()` call **re-launches the whole application**, which calls
`run_sandboxed()` again.

**Measured, not reasoned.** A minimal frozen probe (onedir, 169 MB) produced
eleven levels of nested sandbox working directories —
`alpha_sbx_tiwx7ant\alpha_sbx_0siqqlo5\alpha_sbx_s277jg6k\...` — and left
**four chained `probe.exe` processes** alive (19044 → 31828 → 20260 → 22164)
after crashing on a `PermissionError` while trying to clean them up. The
child processes printed the parent's own banner, which is the direct evidence
of re-entry. The unfrozen baseline of the same probe passes every property.

None of the security checks were even reached: the build never got far enough
to evaluate the audit hook, the taint gate, or resource limits.

**Correct version:** the sandbox must spawn a real interpreter, not
`sys.executable`. Options, none free:

1. Ship a separate embedded Python alongside the frozen app and point the
   sandbox at it. Costs a second interpreter in the bundle.
2. Re-entry guard plus a `multiprocessing` spawn path, which on Windows still
   re-executes the frozen exe and needs `multiprocessing.freeze_support()`
   plus a distinct entry mode.
3. Run model-written code in-process under the audit hook only. **Rejected:**
   it discards process isolation and the timeout, which is most of what the
   sandbox is for.

**Class:** oversight with a hard consequence. `sys.executable` is correct for
every non-frozen deployment and silently catastrophic for a frozen one.

**Status:** blocks the desktop-app branch entirely. Iteration one ends as a
CLI plus a browser console, which is what the evidence supports.

### C27. `python -m build` fails because the repo's own `build/` shadows it

**Reality:** setuptools writes intermediate output to `build/`, and Python
resolves `build` to that directory before the installed package. The error is
`No module named build.__main__; 'build' is a package and cannot be directly
executed`, which names a shadowing problem in language that sounds like a
missing dependency.

**Correct version:** `pip wheel . --no-deps -w dist` is the supported path and
is what `RELEASE.md` and `docs/HANDOVER.md` now document. Renaming the
directory would fight a setuptools convention for no gain.

**Class:** environment trap. Costs the next person ten minutes and points
them at the wrong cause.

### C28. Ollama's two APIs are not the same instrument

**Reality:** B2 assumes an OpenAI-compatible adapter is a protocol swap — same
server, same weights, same run. Measured against Ollama 0.33.3 with
granite4.1:3b on 2026-09-06, using the shipped adapters, that is true for the
things a benchmark scores and false for two things it depends on.

Identical: answer text, `prompt_tokens` (32), `completion_tokens` (12), and
JSON-schema constrained decoding — `format` on the native API and
`response_format.json_schema` on `/v1` both returned `{ "planet": "Jupiter" }`,
and `/v1` accepts the real `ACTION_SCHEMA` in 5.4 s.

**`/v1` reports MORE.** It carries `prompt_tokens_details.cached_tokens`; the
native API carries no such field. On a 206-token prompt built around a fresh
uuid, so the server had provably never seen it, `/v1` reported 5 cached tokens
on first sight and 205 on an identical repeat, while `/api/chat` reported 0
both times. Near-zero then near-total is the shape of real accounting, not a
constant. `RELEASE.md`'s audit recorded the stable-prefix benefit on the local
tier as **unmeasurable for want of cache accounting**; it is measurable, and
was being discarded by the protocol rather than withheld by the server. No
published number moves — `cache_read_tokens` was 0 across all 1179 recorded
calls, so nothing was computed from it.

**`/v1` honours LESS.** `keep_alive` is accepted and silently discarded: unload
the model, send `keep_alive: "30m"`, read `/api/ps`, and the expiry is 5.0
minutes — identical to sending nothing, with HTTP 200 and no warning. A
setting that reads as honoured and is not. On CPU a cold load measured ~30 s
against ~8 s warm, so this is wall clock, the budget that binds here.

**Correct version:** set it on the server. `ollama-serve.ps1` and
`ollama-serve.cmd` now export `OLLAMA_KEEP_ALIVE=30m`. The adapter does not
send the field at all, because sending something the server discards is worse
than not sending it.

**Class:** spec optimism. "OpenAI-compatible" describes a request format, not
a capability set, and the differences run in both directions.

### C29. `usable_context_window` is a claim, not a request, on the OpenAI protocol

**Reality:** the OpenAI chat-completions API has no `num_ctx`. The native
adapter sends one on every call, with a comment explaining exactly why:
Ollama otherwise defaults to a 4096-token window regardless of what the model
supports and **silently truncates**, and M3.4 budgets the context ceiling
against `usable_context_window`, so a smaller server window makes the ceiling
math a lie and the agent loses the head of its own context mid-task.

The `/v1` path cannot send it. Caught in the server log of the first real task
run through `config.openai.yaml`:

    new prompt, n_ctx_slot = 4096, n_keep = 4, task.n_tokens = 1165

against a config declaring 16384. The prompt fit, so nothing was truncated
that time — but the guarantee was absent, and it was absent silently.

**Correct version:** `OLLAMA_CONTEXT_LENGTH=16384` in the server environment,
now set by both launchers and verified in the log (`n_ctx_slot = 16384`).
`config.openai.yaml` states in a banner that the number is a claim about the
server rather than a request.

**Residual, and the honest limit of B2:** it is ONE server-wide value, so
per-backend context windows are not achievable over this protocol against a
single Ollama. Budgeting below what the server provides only wastes window;
budgeting above it corrupts. There is no portable check — the only signal is
`prompt_tokens`, and comparing it to this project's own token count would test
an uncalibrated estimator, which is a check that cannot be trusted. Documented
rather than detected. DEFERRED D16.

It also costs memory: resident went from **2.51 GB at ctx 4096 to 3.61 GB at
16384**, and on this protocol every model the server loads pays it, not only
the backend that needed the window.

**Class:** a capability the abstraction cannot carry. Worth stating plainly
rather than letting "one adapter for everything" imply otherwise.

### C30. The prefix-cache alarm was dead twice over, and one half was a spec assumption

**Reality:** `Telemetry.cache_alarm` has existed since M3.2 as a monitored
invariant — a prefix mutation raises cost roughly 10x, so the ratio collapsing
is the signal. It has never fired, and could not have, for two independent
reasons:

1. No local backend reported cache accounting, so `cache_hit_ratio` was a
   constant 0.0. That is indistinguishable from total prefix mutation, so the
   number could not be acted on either way.
2. The alarm was gated on `paid_tokens > 0`. Under `--profile local` nothing
   costs money, so the gate was never true. **The only machine this project
   runs on is the one machine the alarm could not protect.**

Reason 2 is the interesting one, because the gate was not careless. The spec
justifies this alarm by COST. On this hardware the same mutation is paid in
wall clock instead — re-processing a prompt the server had already cached —
and wall clock is the budget that actually binds here. The gate encoded the
spec's cost axis, and this machine has a different one.

**Correct version:** the ratio is `None` when no backend on the task reports
cache accounting, and a number when one does. The alarm fires on a number
below threshold regardless of whether the tokens were paid for, and never
fires on `None`. Widening when an alarm can fire is a strengthening; the
`None` guard is what stops it becoming noise.

**Class:** spec error of the same family as C23 — a stated criterion measuring
something adjacent to what it claimed. "Caching matters because money" is
true of the spec's deployment and false of this one.

### C31. `cache_hit_ratio: 0.0` meant two different things and a chart could not tell

**Reality:** the field was a float with no null. Every run before 2026-09-06
recorded 0.0, meaning "nothing reported it". A run through the new adapter
recording 0.0 would mean "the backend reported that nothing was cached" —
the actual alarm condition. Averaging or plotting those together invents a
prefix mutation that never happened.

**Correct version:** `float | None`. `None` is unmeasured. Historical runs are
left as they are and read as null rather than being back-filled with a number
nobody measured. `bench/noise.py` records the ratio per task and a run-level
median, and the drop detector medians only over tasks where it was actually
measured.

**Class:** the same distinction as `machine_fault` versus `excluded`, in a
different field. Absence of a measurement is not a measurement of zero.

### C32. Untrusted memory is REFUSED, not queued — a deliberate deviation

**The brief said:** an entry derived from `UNTRUSTED` content "may NOT enter
tier 1 automatically. It requires human approval, routed through the existing
approval queue."

**What was built:** `memory_write` carries `max_arg_taint=USER`, the same
ceiling as `write_file`, so the capability gate REFUSES untrusted-derived
content outright and it never reaches the approval queue.

**Why the deviation.** Queueing means putting a human one click away from
writing attacker-controlled text into the stable prefix of every future
session. The preview a human sees is one line of a tool call; the attack is
designed to look reasonable in one line. That is a bad place to site a
judgement call, and the standing rule is not to weaken a security check to
make a phase work — setting a side-effecting memory tool's ceiling looser
than `write_file`'s would be exactly that, on the one store that is in the
prompt by design.

The brief's own acceptance test says "the tier-1 write is **blocked or
queued**", so refusal satisfies it, and refusal is the stricter branch.

**What is not lost.** Untrusted-derived content still persists — in tier 2,
which is where 3.3 says it belongs. `search_history` retrieves it on demand
and stamps it `UNTRUSTED`, so it enters a later session as data rather than
as the agent's own voice. The difference between the tiers is not whether the
content survives; it is whether it survives as something the model is told to
believe.

**If you want the queue anyway:** it is one line, changing the ceiling to
`UNTRUSTED`, and the existing gate already routes side-effecting untrusted
calls to `needs_approval`. Say so and it changes. It is recorded here as a
deviation rather than done silently.

### C33. Tier 1 memory is disabled during measurement runs, and that is a real gap

**Reality:** `isolate_retrieval` gives a benchmark attempt an EMPTY tier-1
snapshot. So the measured system has the memory TOOLS and none of the
persistence, which is not the shipped system.

**Why it is the right trade.** A fact written by the control arm would
otherwise be in the skill arm's prompt before the model does anything at all
— not a subtle contamination, a direct one. And it compounds: memory persists
across sessions, so a single run would poison every later run of the same
suite.

**Why the snapshot is empty rather than absent, stated precisely.** The
prefix keeps its SHAPE — the same sections in the same order, the same tool
manual. It does **not** keep its size or its content: an empty `<memory>`
block and a populated one differ in bytes like any other text, and the
shipped system's prefix at session 50 resembles the benchmark's at no
session at all.

So the claim is about structure, not byte-level equivalence, and the earlier
wording implied more than it should have. Shape is the right thing to
preserve because shape is what the harness-tax finding implicates — R0 to R5
differ in scaffolding structure, not in how many characters of user content
they carry. But "empty" narrows the gap; it does not close it.

**A prediction this makes, worth checking in Phase 9.** If prefix content
genuinely moves pass rates and not merely prefix structure, then a session
with populated tier-1 memory should differ measurably from one with an empty
block on the same task. Recorded as a research question in
`docs/FINDINGS.md`, not work for now.

**The honest statement of the gap:** this widens the "measured system is not
the shipped system" list that already contains `auto_review`. It is recorded
in every run record under `retrieval_isolation` so a reader never has to
infer it, and `TOOLSET_VERSION` moved to 3 because the tool manual in the
prefix changed even where behaviour did not.

### C34. `ctx.web_fetcher` was pre-set, which made the tool's own path dead code

**Reality:** `AgentContext` assigned `self.web_fetcher = default_fetcher` at
construction, and `tool_web_fetch` began with "if a fetcher is configured, use
it". So the configured branch ran on every call and the tool's own path never
did. Harmless while both paths did the same thing; the moment Phase 4 added
provenance to the tool's path, that path became unreachable code that looked
wired.

**Correct version:** `web_fetcher` is an OVERRIDE SEAM defaulting to `None`,
and the tool owns the default. Every caller in the tree was already
overriding it — five call sites, all tests — so nothing lost a fetcher.

**Class: the adjacency family, SECOND MECHANISM.** Every prior instance was a
CHECK measuring something adjacent to what it claimed — a residual gate below
its own rounding floor, an abort rule counting machine events instead of lost
data, a dead-code audit counting a docstring mention as a call site. This one
differs in mechanism and is identical in signature: a DEFAULT SHADOWING THE
PATH IT DEFAULTS FOR. The branch existed, looked wired, and was unreachable;
the tests were green because both paths returned the same value; and nobody
was testing the property that distinguished them, because until Phase 4 there
was no property that distinguished them.

The shared signature is what to hunt for: **green tests, correct-looking
code, and a property nobody is actually testing.** Recorded as design rule
DR2 in INVARIANTS.

**Sweep for other instances, 2026-09-07.** Three `getattr(ctx, X, None)`
override seams exist in the tree:

* `web_fetcher` — the instance, fixed.
* `web_searcher` — never pre-set, so the default path is reachable. Clear.
* `userdata` in `plan/review.py` — pre-set, but the fallback below it is
  reached through the EMPTY-POLICY case rather than the `None` case, so the
  policy.md path is live and exercised. Checked, not an instance.

Constructor parameters of the form `x or default_x()` (`conn`, `tools`,
`backends`, the verifier registry) are not this pattern: they have one
consumer path rather than two, so there is no branch to be shadowed.

### C35. Budget enforcement granularity is one step, and a step got more expensive

**Reality:** `test_budget_stops_execution` asserted `cost_usd < 0.01` against
a $0.001 budget — a loose sanity bound on overshoot. It began failing at
$0.0100 not because enforcement broke but because the tool manual grew from 8
tools to 16, so the prefix grew, so ONE step costs more.

**Correct version:** the test asserts the mechanism — exactly one model call
against six scripted actions — rather than a dollar figure. Tuning the number
up would have hidden the thing worth knowing.

**What is worth knowing:** budget is checked BETWEEN steps, so the minimum
possible overshoot is one step's cost, and that cost scales with the tool
manual. At `default_task_budget_usd: 0.50` this is two orders of magnitude
away from binding. At a budget near one step's cost, a caller gets one step
regardless of what they asked for.

**Class:** not a defect. A real property that a proxy assertion was hiding,
surfaced because the proxy finally broke.

### C36. The dead-code audit credited method calls to module-level functions

**Reality:** `_unreferenced()` collected every `ast.Attribute` node's `attr`
as a reference. So `policy.describe()` — a method on a class added in Phase
5 — marked the entirely unrelated `profiles.describe` as wired, and it
silently left the unreferenced set.

A FALSE NEGATIVE, which is the dangerous direction for this particular test:
dead code looking wired is exactly what it exists to catch. The same shape as
the docstring bug this function was already fixed for once — the check
measured "the token appears", not "the system calls it".

**Correct version:** an attribute counts only when its receiver is a name
bound by an import in that file, which is what a module-qualified call looks
like. `self.x` and `policy.x` no longer count; `search_mod.search(...)` still
does.

**What it found immediately.** Two genuinely dead functions that had been
invisible:

* `ledger/prefix.freeze_skills` — a second implementation, superseded by
  `AgentContext.freeze_skills`, which the loop actually calls. It looked
  wired because the audit credited `ctx.freeze_skills(task)` to it.
* `kernel/integrity.resume` — **the resume protocol is inert.** DEFERRED
  D20, and the more serious of the two.

**Class:** adjacency again, and worth noting that the instrument catching
adjacency bugs had one of its own. Tightening it paid for itself in seconds.

### C37. Invariant 17 was published and never enforced

**Reality:** invariant 17 says the approved plan hash is verified before every
step. `verify_plan_pin` did two comparisons — the recomputed hash against the
one stored in the `plans` table, and against the copy in the anchor — and the
second was written as `if anchored.startswith("plan:")`.

`checkpoint()` wrote chain heads under the same anchor key, and `run_task`
checkpoints on every run. So after the first step the latest anchor entry was
a chain head, the `startswith` was False, and **the anchor comparison was
skipped silently, every time, for the life of the project** (D21).

**What was actually lost.** The database comparison kept working, so a plan
body edited in the table was still caught. What stopped working is the
out-of-band copy — which is the entire reason an anchor exists. A writer with
database access could edit the plan AND its stored `approved_hash`, and the
check would have agreed with itself.

**Correct version:** plan pins have their own anchor key, so a later
checkpoint cannot displace them. Verified by injection rather than
inspection, because a green suite had proved nothing about this check for as
long as it had existed: `tests/test_plan_pin_is_enforced.py` leaves the
database internally consistent and dissents only in the anchor, and that test
FAILS against the pre-fix code.

**Class:** not a coverage gap. A published invariant that was never enforced,
which is a different and worse thing — the two-touch model rests on it.

**A note on how the test was written.** The first version of the anchor-half
test forged `approved_hash`, which trips the database comparison first and
never reaches the anchor at all. It passed against the pre-D21 code. Running
the new tests against the OLD code is what caught it; without that step the
file would have looked like five green tests covering an invariant that two
of them never touched.

### C38. Streaming removes the wait during generation, not the wait before it

**Reality:** B5 asks for token-by-token output, and the obvious reading is
that a surface stops looking hung. MEASURED against granite4.1:3b on Ollama
0.33.3, warm, two calls differing only in output length:

| | max_output_tokens | total | first chunk | streaming window | silent |
|---|---|---|---|---|---|
| short | 60 | 3.4s | 2.4s | 1.0s | **71%** |
| long | 400 | 3.8s | 2.4s | 1.4s | **63%** |

Time to first token is roughly FIXED and does not scale with output, so it is
prompt evaluation rather than generation. The streaming window grows with
output; the silent head does not shrink. A cold load measured 7.1s of an 8.0s
call, so the fraction is worse on a cold tier.

**Correct version:** streaming alone does not satisfy the intent. The loop
publishes a `waiting` event before dispatching the model call, and the
surface spins the theme's frames until the first token arrives. Without it a
surface reacting only to tokens shows nothing for most of a short turn — the
exact problem streaming was added to solve.

**Class:** not a defect. A claim that was true and insufficient, which is
worth separating from a claim that was false. `docs/evidence/phase7-streaming-latency.json`.

### C39. `stream_events` was briefly changed from "the events" to "events forever"

**Reality:** making the SSE endpoint tail live output meant `stream_events`
looped on a queue rather than returning. `live=True` was the default, so every
existing caller — all of which do `"".join(stream_events(...))` — hung, and
the test suite stopped rather than failed.

**Correct version:** `live=False` is the default and the SSE endpoint opts in.
The in-process callers want the history and an end; only an SSE client wants
the tail.

**Class:** DR2. A default that silently changes what an unchanged call DOES,
so the caller looks correct and behaves differently. Caught immediately here
because the new behaviour was a hang rather than a wrong value — the same
mistake returning a subtly different result would have been much quieter.

### C40. A restriction installed at creation is shorter-lived than what it restricts

**Reality:** Phase 5 gives `ctx.restrict_tools(task_id, policy)`, and the
obvious way to build A4.2's restricted toolset is to call it when the bot
creates the task. That is what a delegated child does and it is correct there,
because a child is created and run inside one process.

A channel task is not. It parks for a human — which A4.2 requires it to do far
more often than a local task — and `_tool_policy` is a dict in process memory.
Restart between the park and the approval and the task comes back
UNRESTRICTED, with nothing in the record saying it ever was otherwise.

**Correct version:** the ORIGIN is a column on `tasks`, and `ctx.tool_policy`
derives the floor from it on every read, unioned with anything narrower. The
fact is stored; the rule is derived. Storing the rule instead would freeze
each task under whatever the policy was on the day it was created, and the
tasks most likely to be sitting parked are the oldest ones.

**Class:** DR1, on an axis the first four instances did not have. Those were a
key too wide. This is a lifetime too short. Recorded in INVARIANTS under DR1
rather than as a new rule, because it is the same question asked about time
instead of space.

### C41. A test that scanned the database file was blind to WAL

**Reality:** `test_the_dropped_message_body_is_nowhere_in_the_database` read
`Path(db_path).read_bytes()` and asserted the marker was absent. The database
runs in WAL mode, so a just-committed row is in the `-wal` sidecar and not in
the main file at all. Injecting the defect — storing the message body verbatim
in place of its hash — left the test GREEN.

**Correct version:** assert on the columns AND scan every file SQLite writes,
after `wal_checkpoint(TRUNCATE)`, with a guard that the scanned set is not
empty. Swept the suite for other database-file byte scans: this was the only
one.

**Class:** adjacency. It measured "the main database FILE does not contain it"
and reported "the DATABASE does not contain it". Found only by running the new
test against the defect it was written for. The same mistake bit a second time
ten minutes later, copying the real measurement database with `cp agent.local.db`
and reading back zero of its 283 tasks.

### C42. `except TaskPaused` around `run_task` is unreachable

**Reality:** `TaskPaused` is raised in the gate and CAUGHT BY `run_task`,
which converts it into a `TaskResult` with `status="paused"` and an
`approval_id`. The bot's first draft wrapped the call in `except TaskPaused`
to send the approval buttons. That handler could never run: the bot would
have reported "paused" and never sent a button, so no channel approval would
ever have been possible.

**Correct version:** read the returned result. `if result.status == "paused"
and result.approval_id`.

**Class:** C34's shape one phase later — a branch that reads as wired and
cannot execute. Caught by reading `run_task`'s handlers rather than trusting
its signature, which is the check C34 said to make.

### C43. Nothing read an approval once it was decided

**Reality:** M12 parks a task for a human and records the decision -
`resolve_approval` updates the row, appends `human.approved`, anchors it. A
resumed task started from its goal; the model re-issued the call; the gate
parked it again under a NEW approval. Measured: run 1 parks, approve, run 2
issues the identical call and parks again, and the file is never written. No
code anywhere read `status='approved'`.

**Correct version:** `_release_decided` at the top of `run_task`, before the
model's first turn, acts on every decided, unconsumed approval: executes it
through the ordinary receipt path, keyed on the parked step so it happens at
most once, or hands a denial back as an observation. Exposure and the gate's
hard denials are re-checked first. `approvals.consumed_at` and
`approval.consumed` record what came of each decision.

**Class:** adjacency in its plainest form - a status value written by three
code paths and read by none. Every test of the approval path asserted that a
decision was RECORDED; none that it had an EFFECT. It also corrects the Phase 8
report: the channel's approval tests pressed approve and then queued a
`<final>`, so none of them ran the approved action. The deputy and
`approve_scoped_writes` allow in the same turn and were never affected, so no
benchmark number rested on a human approval.

### C44. The mock cached regardless of the bytes

**Reality:** `MockBackend` reported a cache hit on every call after the first,
whatever prefix arrived. `test_cache_hit_ratio_above_threshold` - described in
conftest as the only check that prefix stability is observable at all - passed
with the prefix mutated on EVERY call. Injected, and still green.

**Correct version:** the mock's cache is keyed on the prefix bytes; a changed
prefix is a miss and a write. The same injection now turns the test red.

**Class:** adjacency - a stand-in for an independent opinion that had no
opinion. Found while designing 9.6, whose offline half needed a mock that could
be wrong.

### C45. The agent's database was readable as a file

**Reality:** retrieval isolation scoped `search_history` and `expand_artifact`
to the attempt. Nothing protected the database itself. Under
`isolate_retrieval`, a task with `artifact_scope: ["."]` - or none, which allows
every path - could `read_file("agent.db")` and `read_file("agent.db-wal")`, and
read the other arm's goal. No tool call in the 283-task record ever named a
`.db` path, so no published number used it.

**Correct version:** `state_paths()` protects the configured database and every
SQLite sidecar, the anchor, the skills store and tier-1 memory, by resolved
path.

**Class:** adjacency twice over. Isolation scoped the retrieval TOOLS, not the
thing retrieved. And `anchor/` was protected by its conventional NAME, so an
`anchor_path` configured anywhere else was protected by nothing. Found by the
storage-layer sweep C41 asked for, which also found the sandbox copying a
database without its `-wal`.

### C46. The Anthropic adapter would have been refused by its own SDK

**Reality:** the adapter called `messages.create()` non-streaming with
`max_tokens = max(max_tokens, cfg.max_output_tokens)`. The SDK refuses a
non-streaming request whose max_tokens implies more than about ten minutes -
`ValueError`, before anything is sent - so at a large output ceiling the path
would never have made a request. Unverifiable without a key, and a second
unverified claim sitting under the first.

**Correct version:** one streaming path through `messages.stream()`, text deltas
forwarded to `on_chunk`, the final message post-processed exactly as before.
Still UNVERIFIED against the live API; D26 now carries it.

### C47. Phase 9 assumed a conversation the loop does not keep

**Reality:** 9.2 asks for summarization of CONVERSATION HISTORY ONLY, beside
9.1's rule that summarization must never touch tool output. Every ledger entry
construction site in `src/` was checked: the loop appends only OBSERVATIONS -
tool results, verifier and parser notes, refusals, reviews, delegation results,
released decisions. The model's own turns are never ledger entries, and its raw
output is not persisted anywhere (`model.returned` records a token count). The
only notes are the pinned objective and plan. There is no conversation history
distinct from tool output, so 9.2 cannot be met without breaking 9.1.

**What was built:** everything that does not depend on the answer - sessions,
lineage, rotation, the flush, per-session snapshots, the cross-attempt
constraint, the cache boundary - at the compaction point that already existed.
**What was not:** any summarizer. A design STOP; the options are in the Phase 9
report and DEFERRED D30.

**Class:** not a defect in the code; a premise in the specification, found by
checking it rather than building to it.

### C48. The 9.6 prediction failed its depth clause, and the error was the reference

**Reality:** P3 predicted that with tier 1 changed, cache reuse at the boundary
would stop at the first changed byte of the prefix - 1,662 +/- 64 tokens, taken
as the server's prompt-token count for the prefix truncated there, with the
same tools argument. Reuse stopped at 698. P1, P2 and P4 held.

**Correct version:** the served template renders tool schemas AFTER the system
text, so "tokens before byte 2,860 of the prefix" is not "tokens of prefix[:2860]
plus the tools" - the schemas are not before it. Measured in the real order,
reuse is exact to within 2 tokens at three injected divergence points
(`bench/cache_reuse_probe.py`). The prediction's structure held; its reference
did not.

**Class:** adjacency, in my own measurement definition - a reference measuring
something next to what it claimed. Caught because the prediction was committed
before the measurement and its falsifiers were specific enough to be refuted: the
stated reading of a low P3 ("something earlier changed") was contradicted by the
byte comparison the script also made.

### C49. 9.2 is WITHDRAWN - a decision, not a deferral

**By instruction, 2026-09-13, and recorded so no later session reintroduces
it as a missing feature.** 9.2 asked for summarization of conversation history.
The ledger holds no conversation: every other class of content is stored by
reference and projected, and what remains in the tail is tool output (C47). The
summarizer existed to bound context growth, and deletion-based compaction
already bounds it - by the method the research supports and 9.1 protects.
Summarizing here would mean summarizing exactly what 9.1 forbids summarizing:
the two requirements are not in tension, one is the negation of the other
applied to the only content that exists.

**What lineage was for is kept, without a summarizer:** rotation, parent-child
provenance, the flush boundary, a prefix-rebuild point. A child session is
seeded BY REFERENCE: its `parent_id`, the tail as compaction left it
(tombstones, which are references that keep exact strings), and a fresh
tier-1 snapshot carrying whatever the flush persisted. No generated text, so
nothing an exact string can be lost to.

**The one content class where a summarizer could ever be warranted** is
cross-TASK history in a conversation spanning tasks - a Telegram chat, a TUI
session - which does not exist yet: each message is an independent task. If
that history is ever carried, it is human-agent exchange rather than
trajectory, and that is where the question would reopen. Not before.

### C50. The file path went round every control built on the query path

**Reality:** retrieval isolation (the blocker before Phase 3) and argument
scoping (Phase 5) guard the QUERY path - `search_history`, `expand_artifact`,
the arguments a caller passes. The FILE path reached the same bytes four ways,
each measured from a narrow `["work"]` scope under isolation unless noted:

| route | how | reached |
|---|---|---|
| `read_file` | a root-wide or empty scope | every project state file; only the ACTIVE config's db was protected (C45), so `agent.local.db` was open under `config.yaml` |
| `grep` / `glob`, path omitted | the gate never saw the defaulted `"."` | the graders, from a NARROW scope |
| file-glob containing `..` | pathlib follows it | the graders, from a narrow scope |
| `run_code` | the sandbox hook guarded writes, never reads; a subprocess runs outside the hook | anything on the machine, from any scope |

**Correct version:** protected state is enumerated - every config's databases
and sidecars, the anchors, skills (held-out checks included), tier-1 memory and
its blessing, `grading/`, `bench/`, `runs/`, `docs/evidence/`, `.alpha_token`,
`.env`, every `config*.yaml`, `policy.md`, and the task's own `grader_dir`. The
gate checks EFFECTIVE arguments, defaults included; refuses any path that is,
lies inside, or CONTAINS protected state; refuses file-globs with `..`; refuses
`run_code` whose scope would link state into its sandbox. The sandbox child
refuses reads, sqlite connections, directory listings and process starts
against protected paths. `tests/test_state_unreachable.py` covers every
location, every route, from root, empty and narrow scopes.

**The limit, stated:** the sandbox hook lives in the process it polices. It
closes the door to a model that reaches for a file; it is not a boundary
against code written to defeat it (ctypes). That is Phase 6's container,
parked - DEFERRED D32.

**Whether any published number used a file route - verified as far as the
record allows.** `grep` and `glob` were never called: the record's last event
is 2026-09-06, a day before they existed. No `read_file` preview names a
protected location. All 281 measured scope entries are under `work/`. For
`run_code`, only the first 200 characters of each program are recorded: across
all 313, no `sqlite3`, `subprocess`, absolute path, `..` path, directory walk
or grader reference, every literal `open()` target is under `work/`, and no
tool output carries a SQLite header or grader text. That is complete for four
routes and as complete as the record allows for the fifth.

**Class:** a fourth adjacency mechanism, now DR3 in INVARIANTS: a control
guarding one ROUTE to some bytes while another route to the same bytes stays
open. C41 was its storage form (the connection versus the file), C45 its first
file form; this is the general case.
