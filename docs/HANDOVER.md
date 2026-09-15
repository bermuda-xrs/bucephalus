# Handover

> **The aerospace suite is archived.** As of 2026-09-05 `aero_calc` lives
> under `bench/suites/archive/`, `bench/tasks/archive/` and
> `bench/archive/decompose_aero.py`, and is no longer any tool's default —
> `--suite` and `--tasks` are now required arguments. Every result below
> remains reproducible from the archive. Nothing was deleted. See FINDINGS
> 1.13 for why it was retired.

Written for a competent Python developer who has never seen this project.
Everything needed to be productive is here or pointed at. No prior context
assumed.

> **This is the only handover.** A second `HANDOVER.md` used to sit at the
> repository root, describing the project as it stood before the measurement
> work. It was consolidated into this file on 2026-09-01 and deleted: its
> architecture map, verifier table, traps, glossary and claim are now
> sections 17-21 below. The parts of it that had gone stale were dropped
> rather than carried forward, and section 17 lists what was dropped and why —
> including one number that is **withdrawn, not merely stale**.

---

## 1. What this is

**The system.** An agent runtime: you give it an objective, it produces a
plan, you approve the plan once, and it then works unattended until it
reports back. It runs entirely on local models through [Ollama](https://ollama.com)
with no API key and no network. There is a hash-chained event log so a run
can be audited and resumed, a capability gate that blocks tool calls whose
arguments came from untrusted sources, and a sandbox for executing
model-written code. All of that works and is covered by 326 tests.

**The research question.** Published work reports that *human-written*
procedures ("skills") raise a model's task success by roughly +16 percentage
points, while *self-generated* ones average about **−1** — slightly negative.
So the interesting problem is not generating skills, it is building an
instrument honest enough to tell a real improvement from noise. The whole
benchmark apparatus — the paired trials, the statistical gate, the
contamination rules — exists to make that measurement trustworthy. Section 12
explains the method from scratch.

**What has actually been established.** *The research question has not been
answered. No skill has ever been through the full gate, and no skill has ever
been generated.* What the work produced instead is a different and, so far,
more interesting result: **the agent scaffold degrades a model that can
otherwise do the task.** The same 3.4B model, asked the same question,
answers correctly 5 times out of 5 when prompted directly and 0 times out of
5 inside the harness — deterministically, in both directions. Section 6
gives the measurement and the mechanism. Everything else established so far
is a list of defects in the instrument itself (section 9).

---

## 2. Get it running in ten minutes

### Prerequisites

| | |
|---|---|
| OS | Windows (developed and tested here; code is cross-platform, only Windows exercised) |
| Python | 3.11 or 3.12. **Not 3.13+** — package gaps |
| Ollama | ≥ 0.32.7, and it must be **running** (step 3) |
| Disk | ~8 GB for the two models |
| RAM | 15.5 GB is what these numbers came from; see below |
| `ANTHROPIC_API_KEY` | **must be unset.** `--profile local` refuses to start if any provider credential is visible. This is deliberate — a stray key means a run labelled "local" might not be |

**Hardware all numbers in this repo came from:** Intel Core Ultra 5 225U,
12 cores / 14 threads, **15.5 GB RAM, CPU inference** (integrated graphics,
not used). Wall-clock figures do not transfer to other machines.

### Steps

```powershell
# 1. environment. The venv lives OUTSIDE the project on purpose - OneDrive
#    deleted an in-folder .venv twice. This also runs the test suite.
.\setup.ps1                      # expect: 326 passed

# 2. models
ollama pull granite4.1:3b
ollama pull granite4.1:8b

# 3. the model server. LEAVE THIS WINDOW OPEN.
#    It is a dedicated instance on port 11435 with its own model directory,
#    so your existing Ollama on 11434 is untouched.
.\ollama-serve.ps1

# 4. one task, end to end, with a scripted model - no network, no GPU
..\..\..\.alpha-test-1-venv\Scripts\python.exe demo.py
```

`demo.py` should end with something like:

```
gate refused append_to_outbox: append_to_outbox requires <=user arguments,
args are untrusted
message actually sent to the address from the page: no
file written: True
chain verifies: True (first bad seq: None) - paid 4,827 tok - $0.0213
```

That output means the whole pipeline works: it planned, executed, **refused an
instruction embedded in a web page it read**, and the hash chain verifies.
If it does not say that, stop and fix it before anything else.

### Two environment traps

1. **`pip install -e .` does not work at this path.** The directory name
   contains Greek characters and setuptools writes its `.pth` file in the
   system ANSI codepage, which cannot encode them. Use `.\alpha.ps1`; pytest
   gets `pythonpath = ["src"]` from `pyproject.toml`. A normal `pip install`
   of the built wheel works fine — only *editable* installs break.
2. **`.cmd` files cannot carry this path.** `cmd.exe` reads batch files in
   the OEM codepage and `cd /d` fails silently. Use the `.ps1` launchers.

---

## 3. Map of the repo

| Path | What it is |
|---|---|
| `src/agent/` | The system. 64 modules |
| `src/agent/execute/loop.py` | **The spine.** `run_task()` — plan, act, verify, finish. Read this first |
| `src/agent/execute/sandbox.py` | Subprocess + audit hook: writes to scope; agent code's reads to scope plus the interpreter. Advisory - D32 |
| `src/agent/execute/codeact.py` | Runs model-written code; compile-checks it first |
| `src/agent/skills/gate.py` | **The statistical gate.** Paired trials, McNemar, promotion |
| `src/agent/skills/generate.py` | The skill generator (M11). **Has never been run, and refuses to until M7.4 rules 2 and 3 do** |
| `src/agent/assure/verifiers.py` | Graders. `verify_program` is the one the suite uses |
| `src/agent/assure/mesh.py` | Ratchet, anti-gaming signal, held-out registry |
| `src/agent/kernel/` | Event log, hash chain, receipts (exactly-once side effects) |
| `src/agent/security/` | Taint lattice and the capability gate |
| `src/agent/plan/` | Planner (read-only) and the deputy that decides permission boundaries |
| `src/agent/paths.py` | Decides whether state lives beside the source or in user dirs |
| `src/agent/proc.py` | `kill_tree`, orphan reaping, free-memory reads |
| `src/agent/runlock.py` | OS-level lock: one measurement process at a time |
| `src/agent/workspace.py` | Resets a task's workspace so every run starts from identical bytes |
| `bench/noise.py` | Runs the suite N times, classifies each task |
| `bench/run.py` | Paired trials for one skill (`paired` subcommand) |
| `bench/ladder.py` | **The harness-tax instrument.** See section 6 |
| `bench/report.py` | Renders the run sheet; refuses to mix harness versions |
| `bench/coverage.py` | Upper bound on discordant pairs before spending hours |
| `bench/suites/aero_calc.py` | The 30-task benchmark, generated from one table |
| `bench/tasks/aero_calc/` | The emitted task tree (generated; regenerate, don't hand-edit) |
| `grading/<KEY>/verify.py` | **The graders.** Outside `artifact_scope` — see below |
| `work/<KEY>/` | Task workspaces. Wiped and rebuilt before every run |
| `runs/` | Run records. **Gitignored** — copy anything that matters into `docs/evidence/` |
| `docs/evidence/` | Preserved results, including superseded ones with their cause |
| `spec/` | The four original design documents. Read `spec/CORRECTIONS.md` first |
| `tests/` | 326 tests, all offline |

**Why the grader lives outside `artifact_scope`.** `verify.py` contains
`EXPECTED = {...}` in plaintext. It used to sit in `work/<KEY>/`, which is
exactly the directory the agent can read and write. A generated skill whose
procedure was "read verify.py and echo the expected value" would have scored
100% on every task and looked like the largest improvement ever measured.
Only its *text* is now carried into the sandbox; the file is never reachable.
`tests/test_grader_isolation.py` enforces this.

---

## 4. How to run each thing

All commands from the project root. `PY` below means
`%USERPROFILE%\.alpha-test-1-venv\Scripts\python.exe` — that is where
`setup.ps1` puts it (`$env:USERPROFILE\.alpha-test-1-venv`), outside the sync
folder. On this machine that is
`C:\Users\char1\.alpha-test-1-venv\Scripts\python.exe`. Set `PYTHONUTF8=1`
first; the repo path contains Greek characters. (An earlier draft of this
line said `..\..\..\.alpha-test-1-venv`, which resolves correctly only if the
checkout sits at `Desktop\...` rather than `OneDrive\Desktop\...`.)

| Command | Produces | Time |
|---|---|---|
| `$PY demo.py` | End-to-end proof with a scripted model | ~30s |
| `$PY -m pytest tests -q` | 326 tests | ~1.5 min |
| `$PY bench\noise.py --config config.local.yaml --profile local --runs 1 --only E01` | One task, one run, real model | ~3 min |
| `$PY bench\noise.py --config config.local.yaml --profile local --runs 1` | Noise floor, all 30 tasks | **~2.5 h** |
| `$PY bench\noise.py ... --executor granite4.1:8b --budget-scale 3` | Same, on the 8B with scaled time budgets | ~3 h |
| `$PY bench\run.py --config config.local.yaml --profile local paired <skill_id> --suite aero_calc` | Paired trials + gate verdict | **~4 h** |
| `$PY bench\ladder.py --task E01 --model granite4.1:3b --samples 5` | The harness-tax ladder | ~15 min |
| `$PY bench\coverage.py --skill examples\skills\aero_units_discipline.md` | Upper bound on discordant pairs | instant |
| `$PY bench\report.py --out docs\run-sheet.md` | The run sheet | instant |
| `$PY scripts\postinstall_check.py` | Verifies an installed wheel runs a task from another directory | ~20s |

Useful flags on `noise.py`: `--only KEY` (repeatable), `--tier easy`,
`--split eval`, `--resume`, `--executor MODEL`, `--budget-scale N`,
`--skill NAME`.

**Flags on `ladder.py`:**

| Flag | Default | What it does |
|---|---|---|
| `--task KEY` | `E01` | Which suite task to walk. R0-R3 need an entry in `BARE_BY_TASK` (currently `E01`, `E03`, `E04`, `E05`); any other key silently falls back to E01's physics text, which would measure the wrong question - add the task's own bare prompt first |
| `--model NAME` | `granite4.1:3b` | Overrides `backends["cloud"].model`, so it sets the executor for R4/R5 as well as the raw completions at R0-R3 |
| `--samples N` | `5` | Samples per rung |
| **`--rungs LIST`** | `R0,R1,R2,R3,R4,R5` | Comma-separated subset, run in the order given. **This is the flag that makes the ladder cheap.** A full six-rung 5-sample walk is ~15 min; `--rungs R0,R1` is ~4 min and answers "does the tax reproduce" on its own. Whitespace around commas is tolerated |
| `--step-by-step` | off | Appends the reasoning instruction to the contract at R0-R3, and to the task goal at R4/R5. This is the **v3 candidate fix** under test - see section 7 |
| `--out PATH` | `runs/ladder.json` | Resolved against `config.root`. **Change it per run or you overwrite the previous ladder** |
| `--config PATH` | `config.local.yaml` | |

Why `--rungs` matters beyond speed: the rungs are independent measurements, so
a subset is a valid experiment rather than a truncated one. `R0,R1` isolates
the output contract; `R2,R3` isolates the system prefix and the tool manual;
`R4,R5` is the only pair that runs the real loop and the real grader.

**Do not compare across the R3/R4 seam as if it were one scale.** R0-R3 score
the number in the reply *text*; R4/R5 score the *artifact on disk* through
`verify_program`. The gap between them is informative - it separates "can it
compute this" from "can it deliver this" - but it is not a like-for-like
difference, and a rung subset that straddles the boundary is comparing two
different measurements.

Both long runs are **resumable**. They append each finished task to a
`.partial.jsonl` ledger with an fsync, so a kill costs at most one task.
`--resume` continues and refuses to mix results from a different suite,
split, profile or executor.

---

## 5. State right now

| | |
|---|---|
| Branch | `measurement-2026-09-01` (off `main` at `148cd99`; no remote) |
| Harness version | **2** — v3 was evaluated and **rejected**, see section 7 |
| Toolset version | **5** — sympy, `search_history`, memory, web/file tools, then `parallel` |
| Tests | **768 passing, 3 skipped** - the skips are the live-credential tests, until a credential exists (D26) |
| Working tree | clean |
| Running | nothing |
| Last completed step | **Consolidation - four decisions, 2026-09-15.** D36 fixed: every model call made for a task is on the record before it returns (invariants 104-106, C59). D35 option (a): a control drafted from the drafting split and scored on eval only, with the report's caveat read from provenance (107). `max_taint_in` decided DELETE, waiting on a HARNESS_VERSION STOP (D38). Resource limits stated POSIX-only (D37). See CHANGELOG |
| Next step | The triage of the nine unreachable guarantees, then D28's scoping: reports, no code. The user's decisions: `max_taint_in`'s harness bump (D38), the bot token and the API key (D26), Phase 6 (Docker, closes D32) and the Job Object (D37) |

### Iteration two, so far

Phases run in the order 1, 2, 3, with corrections between. Read the CHANGELOG
top-down; each entry states what it invalidates.

* **Phase 1 (B2)** — one `OpenAICompatibleBackend` for the OpenAI
  chat-completions protocol. Model switching is a config edit;
  `config.openai.yaml` runs the same local models through Ollama's `/v1`.
* **Phase 2 (B4)** — FTS5 search over prior sessions, `search_history`.
* **Phase 3 (A1)** — persistent memory in three tiers.

**Three things a new session must not re-derive:**

1. **Ollama's two APIs are not the same instrument.** `/v1` reports
   prompt-cache accounting and the native API does not; the native API
   honours `num_ctx` and `/v1` silently ignores `keep_alive` and has no
   `num_ctx` at all. SPEC-DELTA C28, C29. Start the server with
   `ollama-serve.ps1`, which sets both env vars the `/v1` path needs.
2. **A benchmark attempt can only read what it produced itself.**
   `isolate_retrieval` scopes `search_history` and `expand_artifact` to the
   task tree and empties tier-1 memory. Without it, the skill arm of a paired
   trial can look up the control arm's answer. `bench/noise.py` sets it
   itself, deliberately not from yaml.
3. **A measurement cannot start without declaring its isolation.**
   `bench/measurement.open_measurement` is the only door and `isolate` has no
   default. `None` means undeclared, which is not `False`.
4. **A backend's adapter type never decides behaviour.** Branch on a declared
   capability — `needs_action_envelope`, `reports_prompt_cache` — never on
   `kind ==`. `tests/test_no_adapter_identity_branches.py` fails if a new one
   appears.

### Where the project actually stands

**Iteration one is complete and the research question is not answered.** Those
are both true and the second is not a failure of the first. `RELEASE.md` states
it plainly; FINDINGS 1.13 gives the evidence.

The short version: the reference measurement ran (24.1% raw / 28.0%
timing-clean on the 3B), and then Phase 2B decomposed every failing value and
found that **3 of 19 numeric failures correspond to a trap the suite
declares.** The rest are unstructured. A skill repairs structured error, so no
skill could have been measured to help here — which is a property of the
model–task pairing, not evidence about skills.

**The aerospace suite is archived** under `bench/suites/archive/`,
`bench/tasks/archive/` and `bench/archive/decompose_aero.py`. Nothing was
deleted and every published number remains reproducible. It is no longer any
tool's default: `--suite` and `--tasks` are required arguments.

### What the next iteration needs

Not a bigger suite — a **different** one, and the two constraints below come
from measurement rather than taste:

1. **Run the decomposition pre-test before committing to paired trials**
   (FINDINGS 1.14). If a candidate suite's failures do not decompose, no skill
   has a handle and the experiment cannot produce discordant pairs. Minutes of
   analysis against four hours.
2. **Size it to what a 3B can do in-harness.** E01's goal is 115 words of
   which 10 are physics and 105 are I/O contract. The spec's own stated scope
   for a local tier is "summarizing, sorting, extracting", which this suite
   never was.

Do **not** treat `coverage.py`'s number as a readiness signal. It predicted
20/30 here and the measurement found 3, because declared-trap coverage is not
measured-trap incidence (DEFERRED D12).

### The memory ceiling, measured

At the executor's `usable_context_window: 16384`, `granite4.1:8b` is **7.64 GB
resident**, against 3.6–6.4 GB free depending on what else is running. Loading
it leaves ~0.8 GB against a 2 GB floor, so a full 8B pass pages throughout —
and wall-clock seconds is the only cost axis this project has. The 8B tier is
**not viable on this hardware**, recorded as an evidenced correction to the
spec's laptop-tier assumption (SPEC-DELTA C20).

The 3B is 3.25 GB resident and fits with room.

### Two things that will bite a new session

**A stopped run may not be stopped.** A venv `python.exe` on Windows is a
trampoline, and user-facing stop paths do not route through `kill_tree`
(DEFERRED D10). A manual stop during this iteration left both the wrapper and
the real interpreter running while the ledger kept growing. Check for a live
`python.exe` with `bench/noise.py` in its command line before concluding
anything is idle.

**Long runs are resumable, but only with the seed.** Task order is randomised
and `order_seed` is part of the resume signature, so continuing a killed run
needs `--order-seed <the recorded seed> --resume`. The seed is printed at
start and stored in the ledger's signature line. Without it the run refuses to
resume rather than blending two orders — which is the guard working, not a
bug.


---

## 6. The primary finding — the harness tax

### What this is

The same model, asked the same question, gets it right outside the agent and
wrong inside it. **Deterministically, in both directions.** This is not noise
and not task difficulty. The scaffold is degrading a model that can do the
work.

`bench/ladder.py` is the instrument. It asks one question at six escalating
levels of scaffolding and reports correctness at each. The rung where
correctness collapses localizes the cause.

| rung | what is added |
|---|---|
| R0 | bare question, plain text |
| R1 | R0 + the output contract (file path, exact key name, SI units) |
| R2 | R1 + the system prefix, still a single turn |
| R3 | R2 + tool schemas in context, still a single turn |
| R4 | the full agent loop, one step allowed |
| R5 | the full agent loop, unrestricted |

### The result

`granite4.1:3b`, task E01 (circular orbital velocity at 400 km altitude;
expected **7672.6 m/s**, tolerance 0.5%), 5 samples per rung, temperature 0.

| rung | prompt | correct | answer written | response | time |
|---|---|---|---|---|---|
| R0 | 213 ch | **5/5** | 7668.0 | 1155 ch | 45.7 s |
| **R1** | 456 ch | **0/5** | 7.784152678 | **236 ch** | **7.7 s** |
| R2 | 1604 ch | 0/5 | 7.784 | 32 ch | 14.3 s |
| R3 | 2423 ch | 0/5 | 7.7841017306 | 61 ch | 13.9 s |
| R4 | loop | 0/5 | 11185784.5 | — | 88.3 s |
| R5 | loop | 0/5 | 11185784.5 | — | — |

**The collapse is at R1.** R0 → R1 adds only the output contract: the
`answer.json` path, the exact key name, "SI base units", and "state the final
number in your reply as well". 243 characters. No system prompt, no tools, no
loop.

### Verbatim, because the difference is the whole finding

**R0** — four steps, correct radius, right answer:

> To compute the circular orbital velocity \(v\) … **Step 1: Calculate the
> orbital radius \(r\).** Earth's radius \(R_{Earth}\) = 6,371,000 m.
> Altitude = 400 km = 400,000 m. \(r\) = 6,371,000 + 400,000 = **6,771,000 m**
> … **Step 4: Take the square root.**

**R1** — no working at all:

> `v_circular_m_s: 7.784152678`
>
> This is the circular orbital velocity for a satellite at an altitude of 400
> km above Earth's surface, calculated using the gravitational parameter
> (μ = 3.986004418e14 m^3/s^2) and Earth's radius (6371000 m).

### The mechanism: answer position, not chain-of-thought suppression

> **Superseded 2026-09-03 by Phase 1 (G3).** This section originally read
> "the mechanism: chain-of-thought suppression", citing the collapse in
> response length. That is a **correlate, not the cause**, and the corrected
> version below is both narrower and better evidenced. See FINDINGS 1.6.

Response length collapses **1155 → 236 → 32** characters and time
**45.7 s → 7.7 s → 1.4 s**. At R0 the model reasons in four steps and gets the
radius right. From R1 it emits an answer immediately with no working, and a
3.4B model doing that arithmetic in one shot gets it wrong.

It is **not context dilution** — R1's prompt is 456 characters. It is **not
tool distraction** — no tools appear until R3.

**But length is downstream of something more specific.** The discriminator is
**where the answer appears in the response**:

* every **failing** response emits `key: value` *before* any working;
* every **passing** response emits the number *last*.

E04's R1 failure shows this cleanly: it states `delta_v_m_s: 8860`, then
writes the **correct** formula and the **correct** substitution, and never
evaluates them. The working is post-hoc rationalisation anchored to a token
already in the context, which autoregression cannot revise. A response that
commits to a number first has nothing left to write — hence the shorter
length.

**E05 is the within-task control.** Same task, same rung, same prompt bytes,
temperature 0, and it produced *both* behaviours across three samples: the
answer-first sample (362 ch) was wrong, the two answer-last samples (685 ch)
were right. That isolates answer position from task identity, from rung and
from prompt in a single comparison — which the length story cannot do.

Two consequences. The fix to aim for is **not** "make the model reason more"
but "stop it emitting the answer token first". And the wall-clock half of the
figures above is partly an artifact: `reap_orphans()` kills the warm model
between runs, so R0's first sample always absorbs a cold load. The drop
survives on warm samples (E04: 24.3 s → 12.0 s), but **45.7 s → 7.7 s should
not be quoted as a pure effect size.** See FINDINGS 1.10.

### The confirmatory probe

A collapse at the first rung could have been a badly written prompt, so:

| variant | correct | response |
|---|---|---|
| R1, contract only | **0/5** | 236 ch |
| R1 + *"work through it step by step … and only then give the final number"* | **4/5** | 882–1022 ch, answer 7667.0 |

Same contract, one added instruction. **The capability was never absent; the
contract was suppressing it.** The tax is real and it is fixable.

### Caveat

R1's contract is a **faithful reduction** of the suite's goal text, not its
verbatim copy. The real goal carries *more* plumbing — the `constants.json`
layout, "use `run_code` to read the inputs, compute, and write the answer file
in one program". So the deployed harness is **at least as suppressive** as
R1, not less.

### Reproducing it, including on another model

```powershell
$PY bench\ladder.py --task E01 --model granite4.1:3b --samples 5
$PY bench\ladder.py --task E01 --model granite4.1:8b --samples 5 --rungs R0,R1
```

Output goes to `runs/ladder-*.json` with every prompt and every response
stored verbatim. Preserved copies: `docs/evidence/ladder-E01-3b.json` and
`ladder-E01-3b-analysis.json`.

---

## 7. The v3 decision — SETTLED 2026-09-03: not applied

**The change.** One line in the goal template in `bench/suites/aero_calc.py`:
an instruction to work step by step before giving the final answer.

**What it would fix.** The probe above: 0/5 → 4/5 on the 3B for E01. If it
generalizes, it may substantially raise every 3B number in the project.

**What it would void.** Everything measured under harness v2, because it
changes what the model is asked to do. That includes B1's 0/5 and the D2
re-grade. `HARNESS_VERSION` would go to 3 and `bench/report.py` would then
refuse to plot v2 and v3 points together — by design.

**Why it is unapplied.** Three reasons, all for the next person to weigh:

1. It has been tested on **one task, one model, one rung** (R1, not the full
   loop). It may not survive R4/R5, where the system prompt and tool schemas
   also compete for the model's attention.
2. It would be the **third time** difficulty was adjusted before the cause was
   understood. The first two — rebalancing the suite, then re-tiering it —
   were both wrong levers.
3. It reframes an earlier conclusion. "The suite is bimodal, the 8B aces it
   and the 3B fails it" may be a property of the **scaffold**, not of the two
   models. Rebuilding the suite around a spread that a one-line prompt change
   might erase would waste the rebuild.

**What would settle it:** run the ladder's R4/R5 rungs with the step-by-step
instruction included. If correctness survives into the full loop, apply v3. If
it collapses again at R4, the contract is only part of the cause and the rest
is in the system prefix or the tool schemas.

### The answer, 2026-09-03

That probe was run: `bench/ladder.py --task E01 --model granite4.1:3b
--samples 5 --rungs R4,R5 --step-by-step`. Evidence in
`docs/evidence/v3probe-ladder-E01-3b-R4R5-sbs.json` and
`docs/evidence/phase1-analysis.json`.

**R4 0/5, R5 0/5. v3 is NOT applied. `HARNESS_VERSION` stays 2.**

Two independent routes reached the same verdict:

* **The fix does not survive.** 0/5 at R5, the valid rung.
* **The tax does not generalize.** G3 reproduced the R0-correct /
  R1-collapsed pattern on only **1 of 3** tasks (E04; E05 partially; E03 not
  at all), so the premise that v3 would "substantially raise every 3B number"
  does not hold either.

**Reason 1 above was the right worry and it was vindicated** — one task, one
model, one rung did not survive contact with the loop. **Reason 2 was
withdrawn before the run** as a category error: rebalancing and re-tiering
moved the target, whereas v3 repairs the instrument, and those are different
things. **Reason 3 is now partly answered** by G4 — the 3B/8B spread at R1 is
real and capacity-linked, not purely an artifact of the scaffold.

**R4 turned out not to be a valid rung at all.** It sets `max_steps=1`, so it
scores *whether the model spent its one action on `run_code`* rather than
whether it can deliver. Four of five samples spent their step on
`read_file(params.json)` — correct behaviour — and scored zero in 7.4 s. The
bias runs against the intervention, since "work through it step by step" is
an instruction to take preparatory steps. **The v2 R4 row in section 6
carries the same defect** and should not be read as evidence the tax survives
into the loop. See FINDINGS 1.9.

**What v3 did do**, recorded because it is a real effect that is *not* a
pass: it improved error **structure** 3.6x on the monotonic scorer while
leaving the pass rate at 0/5. The v2 answer 11,185,784.5 (145689% out, score
0.077) became 7909.544108195981 (3.09% out, score 0.279) — which is
`sqrt(mu/(6371000+400))` exactly: the model reads `altitude_km: 400` and adds
**400 metres**. That is the suite's own km-to-m trap, so v3 makes the suite
measure what it was designed to measure. It still fails. It is not a pass and
must not be tuned into one.

---

## 8. Numbers that are void, and why

**The rule: results from different harness versions must never appear on the
same chart.** A pass rate is an answer to "what counts as a pass", and that
definition changed. `bench/report.py` enforces this — see
`test_mixing_harness_versions_is_refused_not_plotted`; the generator prints
**REFUSING TO PLOT** and drops the points rather than drawing a line through
them.

| number | harness | status | why |
|---|---|---|---|
| Original pass 1: **10%** over 30 tasks | v1 | **VOID** | Grader inside `artifact_scope`; workspace never reset between runs; `max_steps` 6; verification only on `<final>`. 48% of failures never attempted the task |
| D2: **20%** (1/5, 8B easy tier) | v1 | **VOID** | Same loop. Three tasks wrote a *correct* answer and were scored as failures because the model never said `<final>` |
| D2 re-grade: **80%** (4/5) | mixed | **Not chartable** | v1 artifacts graded by v2 rules. Honest as a *re-grade of specific files*, but it is not a v2 measurement and must not sit beside one |
| B1: **0/5** (3B, E01) | v2 | **Valid for v2**, superseded in interpretation | A correct v2 measurement. The ladder explains it rather than voiding it. **Would be voided by v3** |

Everything above is preserved in `docs/evidence/` with its cause recorded.

---

## 9. Bugs found, as two classes

### Class 1 — instrument bugs: the measurement was of something adjacent to the claim

| bug | what it was | how it was caught | what it invalidated |
|---|---|---|---|
| **Grader leak** | `verify.py` with `EXPECTED` in plaintext sat inside `artifact_scope` | Reading the fixture list while checking a different question | Nothing yet — no model was clever enough to exploit it. It would have invalidated any *generated* skill result |
| **Workspace not reset** | Both arms of a paired trial shared one directory, and `verify_program` grades files on disk | Asking why `TaskSpec` had no `fixtures` field | Would have let the control arm be graded against the answer the skill arm just wrote — manufacturing the exact effect the gate exists to detect |
| **Announcement vs work** | Verification ran only inside the `<final>` branch, so a task that wrote a correct answer and ran out of steps was never graded | Reading D2's artifacts on disk and finding 7672.598648385013 scored zero | Every pre-fix pass rate, including the 10% and the 20% |

### Class 2 — process bugs: a parent died and its child kept running

| bug | what it was | how it was caught | what it invalidated |
|---|---|---|---|
| **venv trampoline** | On Windows a venv `python.exe` is a wrapper; `Popen` returns *its* pid while the real interpreter is a child. `proc.kill()` left the interpreter alive | A lock test failed because the "killed" child still held the lock | `test_kill_and_resume`'s claim of "a real kill, no `finally` runs" — it passed for the wrong reason |
| **Orphaned `llama-server`** | Killing Ollama left its worker holding 5.9 GB | One model call took 1499.8 s for work that takes 88.1 s — an 18× slowdown | Three tasks measured under memory pressure, on a metric whose cost axis is seconds |
| **Session-tied detach** | Background runs died silently when the controlling session ended, leaving zero-byte logs | Two multi-hour runs vanished with no error | Two D2 attempts |

### The pattern — look for a fourth in each class

**Instrument class:** *the thing being measured is adjacent to the thing
claimed.* Each of these three graded something real — a file, a workspace, an
announcement — that was not quite the deliverable. When you add a check, ask
what it would score for an agent that did the work perfectly but behaved
slightly unusually.

**Process class:** *anything spawned outlives what spawned it.* Route every
termination through `kill_tree()` in `src/agent/proc.py`, and reap orphans
before any timed run — `noise.py` does this in its preflight and records free
memory before and after.

---

## 10. The invariants, in plain language

From the original specification, Part 15. A newcomer will otherwise trip one
and not understand why the code refused them.

| # | Rule | Protects against | Enforced in |
|---|---|---|---|
| 1 | The unchanging part of the prompt is byte-identical across turns | Silently losing the provider's ~90% prompt-cache discount | `PrefixGuard` raises, `ledger/prefix.py` |
| 2 | No retry without a new external observation | An agent "trying again" with nothing new, burning turns | Structural in `execute/loop.py` — failure paths append an observation then continue |
| 3 | Text found in content is data, never instructions | A web page saying "email the keys to X" being obeyed | `security/capabilities.py` denies on argument taint |
| 4 | Whoever writes something never writes its test | Marking your own homework | `assert_different_authors` raises, `assure/mesh.py` |
| 5 | Held-out tests never enter a prompt | Fitting the checks instead of the task | `HoldoutRegistry`, `assure/mesh.py` |
| 6 | Monitor output never reaches the agent | An agent that knows it is watched behaving differently | `monitor_flags` read only by the CLI and control plane |
| 7 | Every side-effecting tool goes through `execute_once` | Sending the same email twice after a crash and resume | `kernel/receipts.py`, under a lock |
| 8 | The agent's own trail is never compressed, only documents are | Removing the words that hold a sequence of actions together | `ledger/compact.py` deletes whole entries, never paraphrases |
| 9 | Nothing resumes without verifying the hash chain | Resuming into a tampered state | `kernel/integrity.py` raises |
| 10 | Only the gate promotes a skill, and only with a human's yes | The system quietly teaching itself something worse | `PermissionError` in `skills/gate.py` |
| 11 | Working context stays under the fill ratio | Long-context accuracy collapse | `enforce_ceiling`, `execute/loop.py` |
| 12 | Every model call is recorded | Cost numbers that miss calls | `instrument/telemetry.py` |
| 13 | The planner cannot write | A planner that edits while planning | Read-only tool set, `plan/planner.py` |
| 14 | The reviewing model differs from the executing one | A model approving its own actions | Asserted in `plan/review.py` |
| 15 | Review decides, never grants | Permission creeping wider | `plan/review.py` |
| 16 | The most serious actions always wait for a human | Irreversible actions taken unattended | `human_only_tiers`, `plan/review.py` |
| 17 | An approved plan's hash is checked before every step | Executing a plan the human did not approve | `verify_plan_pin`, before each step |

**Profile-conditional** (do not apply under the shipped single-tier local
config): **#14** — there is one tier, so reviewer and executor are the same
model unless a second is configured.

**Currently inoperative:** the anti-gaming veto that compares visible against
held-out pass rates (part of #5's purpose). `record_holdout_gap` has no
callers, and the benchmark has no visible/held-out split, so it cannot fire.
Every promotion therefore carries `gaming_check: "NOT MEASURED"` rather than
silently appearing to have passed a check that never ran.

Additional invariants added by this work are in `INVARIANTS.md` (26–29:
per-task durability, resume never blending measurements, task-level trial
counting, identical bytes at the start of every arm).

---

## 11. What has never been measured, and why

This is the honest scope of the project.

| Component | Status | Why |
|---|---|---|
| **M7 ratchet** (keep a revision only if it scores better) | Barely exercised | The grader returns a single number with no direction or magnitude. Of 30 tasks in the original run, exactly **one** ever moved its score. A monotonic scorer now gives the ratchet an ordering, but the model still gets no actionable feedback |
| **M7.4 anti-gaming veto** | Never fired | Needs a visible/held-out split of checks, which only the unrun skill generator produces |
| **Prompt-cache benefit** | Unverifiable locally | `cache_read_tokens` is populated only by the mock backend. Across 566 real local calls it is 0. Ollama has no prompt-cache accounting — the ratio is *unknown*, not zero |
| **Hybrid routing** | Implemented, tested, unexercised | `config.local.yaml` maps the "cloud" slot to a local model. There is one tier. The 8B alone is 7.72 GB resident against 15.5 GB total, so two models cannot be co-resident and a routed run would measure model swapping |
| **`auto_review` (the deputy)** | Off in every benchmark run | Deliberate: a model in the decision path is variance in the signal being measured. It means the measured system is not the shipped system, and the report header says so |
| **Escalation by disagreement** | No call site | Would need a second tier. `Router.choose` records the gap in the run provenance when a second tier is configured |
| **The Anthropic cloud adapter** | Never made a live call | Written and unit-tested only. **Do not describe it as working** |

---

## 12. How the experiment is supposed to work

Read this before interpreting any number in the repo. It is self-contained;
the original design documents are in `spec/` if you want the reasoning behind
each choice.

**A skill** is a short written procedure — at most three modules — that gets
prepended to the model's prompt. The example in
`examples/skills/aero_units_discipline.md` tells the model to convert units
first, identify which body a problem concerns, and check its answer's
magnitude against a known reference.

**A paired trial** runs the *same task twice*: once with the skill loaded
(the "skill arm") and once without (the "control arm"). Both arms get an
identical, freshly rebuilt workspace. Pairing matters because tasks differ
enormously in difficulty — comparing "30 tasks with a skill" against "30
different tasks without" would mostly measure which tasks landed where.

**Only disagreements carry information.** After N paired trials each task
falls into one of four boxes:

| | control passed | control failed |
|---|---|---|
| **skill passed** | concordant — tells you nothing | **b** — skill wins |
| **skill failed** | **c** — control wins | concordant — tells you nothing |

Tasks that both arms pass, or both fail, cancel out. Only **b** and **c** —
the *discordant* pairs — say anything about the skill. This is **McNemar's
test**: under the null hypothesis that the skill does nothing, a discordant
pair is equally likely to fall in b or c, so the question is whether b
exceeds c more than chance allows. It is a one-sided binomial test on b out
of (b+c).

Roughly **75–80% of disagreements must favour the skill** at any n:

| discordant pairs | needed in b | p |
|---|---|---|
| 10 | 9 | 0.011 |
| 13 | 10 | 0.046 |
| 15 | 12 | 0.018 |
| 20 | 15 | 0.021 |

**The gate promotes only if all four hold** (`config.local.yaml`, `gate:`):
at least 15 paired trials, at least 10 discordant pairs, lift ≥ 5 points, and
p < 0.05. `min_discordant_pairs` is the load-bearing one; `min_lift_pp` is
**inert** below n=160 at these settings and is not protection. Two vetoes sit
on top: a catastrophic regression on a flagged task, and a large gap between
visible and held-out pass rates (currently inoperative — section 11).

**Trials are counted per task, never per run.** Running one task twice with
different seeds does not create two pairs — two runs of the same task share
its difficulty, so counting them separately inflates n without adding
information and makes the p-value anti-conservative.

**The eval split is sacred.** The 30 tasks are split 15 "drafting" / 15
"eval". A skill *generator* may look at drafting tasks only. If a skill were
drafted from a task and then scored on it, the measured improvement would be
partly memorization. `--split eval` on `bench/run.py paired` enforces the
boundary; `check_contamination` catches only tasks named in a skill's
provenance, which a hand-written skill has none of.

**The calibration skill is a positive control, not a result.** It is
hand-written by someone who saw the entire suite, so no split is clean with
respect to its author. Running it answers one question: *does the detector
fire when a real effect exists?* If the gate cannot detect a
deliberately-good skill, then every later rejection is uninterpretable — a
gate that rejects everything and a gate that works look identical until this
passes. Its lift is **not** evidence that skills generalize, and the
contamination caveat must appear in the same paragraph as the number.

---

## 13. Next steps, in order

| # | Step | Compute | Status / what it would establish |
|---|---|---|---|
| 1 | ~~**G3** — ladder R0/R1 on E03, E04, E05 with the 3B~~ | ~20 min | **DONE 2026-09-03.** The tax reproduces on **1 of 3**. Not general. Mechanism corrected to answer position. FINDINGS 1.6 |
| 2 | ~~**G4** — ladder R1 on the **8B**, E01~~ | ~15 min | **DONE.** 8B is **5/5** where the 3B is 0/5. Capacity threshold. FINDINGS 1.7 |
| 3 | ~~**The v3 decision**~~ | ~20 min | **DONE. v3 REJECTED**, R5 0/5. Harness stays v2. Section 7, FINDINGS 1.8 |
| 4 | **H1** — full 30 tasks on the 8B, harness v2 | **~3 h** | **BLOCKED ON MEMORY** (section 5). The medium and hard tiers have never run on the fixed harness. Still the single most informative run available |
| 5 | **Calibration + ablation** | ~4 h | See below. Coverage upper bound is **20/30** against a gate floor of 10, so the floor is reachable — easy 9/10, medium 7/12, hard 4/8 |
| 6 | **M11** — generate one skill, gate it on eval only | ~4 h | The actual research question |

**Two new defects from Phase 1 change how later steps should be read**, both
in FINDINGS: `reap_orphans()` kills healthy workers (1.10), and temperature 0
is **not** byte-deterministic on this stack (1.11). The second one matters
most for planning: any argument that repeat passes carry no information
because warm runs are byte-identical does **not** hold. Cold-start repeats
are the right variance instrument.

**Protect the ablation if anything gets cut.** It removes module 3 (the
magnitude check) from the calibration skill and re-runs the paired trial. If
the lift collapses without it, the skill works by *supplying the external
check the bare agent lacks* — that is a mechanism, not a score, and it would
compete with the harness tax for the headline. Choose the tier with the most
room first: if the 8B is already near-ceiling on easy tasks, the ablation has
no discordant pairs to find there.

**On M11:** the published baseline for self-generated skills is **−1.3
points**. Rejection is the *expected* result and is a valid finding. Do not
tune the gate to make something pass.

---

## 14. House rules

1. **Never tune a threshold to make a result pass.** If a number is
   disappointing, report it. Two threshold hazards have already been caught
   here — both times by noticing that a change would raise pass rates and
   stopping to check why.
2. **Preserve superseded results** in `docs/evidence/` with the cause and the
   harness version that produced them.
3. **Copy anything from `runs/` into `docs/evidence/` and commit before a
   long job.** `runs/` is gitignored; a crash loses it.
4. **Reap orphans and check free memory before every timed run.**
   `noise.py` does this automatically and records both. If free memory is
   under ~2 GB, stop and find the orphan.
5. **Never run anything else during a timed run.** The test suite alone took
   8 minutes under contention instead of 1.5, and contention can manufacture
   a `budget_exhausted` result on a task that would have finished. The cost
   axis is wall-clock seconds; contention corrupts the only axis there is.
6. **One measurement process at a time.** Enforced by an OS lock
   (`src/agent/runlock.py`), which releases automatically when the process
   dies.

---

## 15. Pointers

| Document | Contains | Read it when |
|---|---|---|
| `spec/CORRECTIONS.md` | Errata for the original specification — a model that does not exist, the wrong tokenizer, an API parameter that now returns 400, an inverted comparison, a config that trips its own assertion | **Before** opening `spec/BUILD-SPEC.md` |
| `spec/BUILD-SPEC.md`, `spec/EXPLAINER.md` | The original design documents, preserved unedited. The reasoning and the published evidence behind every invariant | You need to know *why* a rule exists before changing code near it |
| `docs/SPEC-DELTA.md` | Every place the spec is now known to be wrong, with the correct version and whether the divergence was deliberate, an oversight, or a workaround | Before trusting any spec statement |
| `docs/FINDINGS.md` | What the numbers show, what they do not, and what would change the author's mind — with acceptance criteria written in advance | Before quoting any result |
| `docs/evidence/` | Preserved run records, including aborted and superseded ones, each labelled with its cause and live defects | You need the raw data behind a claim |
| `INVARIANTS.md` | The pre-commit checklist, 29 items, each naming the test that catches a violation | Before every commit |
| `CHANGELOG.md` | What changed and why, including a same-day correction of a claim that turned out to be wrong | Orienting on recent history |
| `RELEASE.md` | What "first iteration complete" means, as a checklist | Planning a release |

---

## 16. Open questions

1. **Does the harness tax generalize?** One task, one model, one rung. G3 and
   G4 answer it cheaply and should be done first.
2. **Where exactly does reasoning die?** The step-by-step instruction restores
   correctness at R1. Does it survive R4, where the system prefix and tool
   schemas are also present? If not, the output contract is only part of the
   cause.
3. **Is "the suite is bimodal" real, or an artifact of the scaffold?** The 8B
   scores 4/5 and the 3B 0/5 on the same easy tasks. If a prompt change moves
   the 3B, the spread was never a property of the models.
4. **Why can the 8B not terminate?** It writes correct answers and never emits
   `<final>`; under the tag protocol it never emitted one at all across 8
   tasks, while the 3B managed both protocols with byte-identical config.
   Under the two-touch model an agent that never says "done" hangs — this is
   an operational hazard, not just a benchmark artifact.
5. **Is this task family right at all?** The original design says a small
   local model's scope is "summarizing, sorting, extracting". The benchmark
   asks for multi-step numerical reasoning *and* program synthesis *and* file
   I/O. That is the spec contradicting itself, and the harness tax may be the
   consequence.
6. **Is `done_unannounced` the right default?** It counts as a pass because
   the artifact is correct. The alternative view is that failing to terminate
   is a real failure and should be scored as one. Both counts are reported
   separately so the choice stays visible.

---

## 17. Architecture

Merged from the root `HANDOVER.md` when the two files were consolidated.

The system is 13 milestones (M1-M13) from the spec. The build order is not
arbitrary — several later components are unsafe or unmeasurable without
earlier ones.

```
                    +------------------------------+
   console (:8901)  |  M12 control plane (FastAPI) |  CLI (alpha.ps1)
   web UI --------->|  + M13 plan / deputy         |<----------
                    +---------------+--------------+
                                    |
                    +---------------v--------------+
                    |  M4 the loop (execute/loop)  |
                    |  plan -> act -> verify -> ratchet
                    +---+--------+--------+--------+
                        |        |        |
        +---------------v--+  +--v-----+ +v-----------------+
        | M8 capability    |  | M6     | | M7 verification  |
        | gate + taint     |  | router | | mesh + ratchet   |
        +-------+----------+  +--+-----+ ++-----------------+
                |                |        |
        +-------v----------------v--------v-----------------+
        | M1 kernel - event log, hash chain, receipts       |
        | M2 telemetry - M3 context ledger                  |
        +-----------------------+---------------------------+
                                |
                    +-----------v------------+
                    | SQLite (agent.local.db)|
                    +------------------------+
```

### Module map

Complements the file-oriented map in section 3; this one is keyed by
milestone, which is how the spec refers to things.

| Path | Milestone | What it does |
|---|---|---|
| `kernel/db.py` | M1 | Schema. **Never UPDATE/DELETE `events`.** |
| `kernel/events.py` | M1 | Append-only log + SHA-256 hash chain |
| `kernel/integrity.py` | M1 | The anchor; resume halts on mismatch |
| `kernel/receipts.py` | M1 | `execute_once` — side effects at most once |
| `instrument/telemetry.py` | M2 | Per-call cost; observed throughput |
| `ledger/prefix.py` | M3 | Cache-stable prompt prefix + guard |
| `ledger/compact.py` | M3 | Compaction by **deletion**, never summarization |
| `ledger/semantic_cache.py` | M3 | Cached operations; refuses to launder taint |
| `models/` | M4 | ollama / anthropic / mock backends |
| `execute/loop.py` | M4 | **The spine. Read this first.** |
| `execute/sandbox.py` | M5 | Subprocess + audit hook + live tool bridge |
| `execute/tools.py` | M5/M8 | Tool registry and the taint table |
| `router/cascade.py` | M6 | Allowlist routing, escalation by disagreement |
| `assure/verifiers.py` | M7 | The verifier registry (see section 18) |
| `assure/mesh.py` | M7 | Ratchet, anti-gaming, held-out registry |
| `assure/monitor.py` | M8.3 | Silent monitor — **never visible to the agent** |
| `security/taint.py` | M8 | Provenance lattice: trusted < user < untrusted |
| `security/capabilities.py` | M8 | The gate. Every tool call passes here |
| `skills/gate.py` | **M9** | **The statistical gate. The heart of the project.** |
| `skills/generate.py` | M11 | Generator — cannot promote, by construction |
| `delegate/contracts.py` | M10 | Five-gate test; most delegations refused |
| `plan/planner.py` | M13 | Read-only planning; plan hash pinned |
| `plan/review.py` | M13.3 | The deputy |
| `control/server.py` | M12 | HTTP API |
| `control/guard.py` | — | Auth, CSRF, DNS-rebinding protection |
| `userdata/store.py` | — | User-authored suites/policy/grounding |
| `profiles.py` | — | Enforced local/mixed backend profiles |

### What was dropped in the merge, and why

The root file was written before the measurement work and several of its
claims had gone stale. They are recorded here rather than silently deleted,
because a reader who saw the old file should be able to find out what
happened to them.

| Root claim | Status |
|---|---|
| "215 tests passing", "27 commits" | Superseded. 326 tests; see section 5 |
| "The instrument validated once at small scale — 3 tasks, 0/3 baseline to 3/3 with the calibration skill, 3/3 discordant in the skill's favour" | **Withdrawn, not merely stale.** It was measured before the workspace-reset defect (SPEC-DELTA C12) was found. Both arms shared one directory and `verify_program` grades files on disk, so the control arm could be graded against the answer the skill arm had just written — which manufactures exactly that result. It is not evidence and must not be quoted |
| Gate table showing ">= 30 paired trials" | Superseded by `config.local.yaml`, which is 15. Section 12 carries the current thresholds and why the change is strictly harder, not weaker |
| "Plus two vetoes ... visible/held-out pass gap > 0.25" | Written as if operative. It cannot fire — see section 11 and CORRECTIONS 2.8 |
| "No LICENSE. A deliberate omission" | Stale. Apache 2.0 is in the tree |
| "`INVARIANTS.md` has all 25" | Stale. 29 |
| Root sections 8-9 ("what is incomplete", "the next three things to do") | Superseded wholesale by sections 11 and 13 of this file |

---

## 18. Verifiers — what "passed" means

In `assure/verifiers.py`. **The choice of verifier decides whether the
benchmark measures anything.**

| Verifier | Checks | Use for |
|---|---|---|
| `verify_program` | Runs the task's own `verify.py` against files on disk | **The eval suite.** Strongest |
| `expected_value` | Answer equals a known value (`x-expected`) | Numeric/string answers |
| `contains_all` | Required substrings survived (`x-contains`) | Prose with checkable facts |
| `execution` | Code runs, exit 0 | Code-writing tasks |
| `json_schema` | **Shape only** | Almost never in an eval suite |
| `always_pass` | Nothing | Refused by the suite validator |

**The trap:** `json_schema` accepts `{"total": 0}` for an invoice totalling
4825. A suite scored that way reports a high pass rate that means nothing —
the same failure an audit found in ~59% of SWE-bench Verified's hardest
tasks. The console shows a running count of how many tasks cannot catch a
wrong answer.

Two consequences that are easy to miss:

* `always_pass` grades nothing, so `_final_sweep` in `execute/loop.py`
  deliberately refuses to sweep it. Sweeping a task that grades nothing would
  turn every budget exhaustion into a pass.
* `verify_program` grades **files on disk**. That single property is the root
  of both instrument defects in section 9: it is why the workspace must be
  rebuilt from identical bytes before every arm of every run, and why the
  grader must live outside `artifact_scope`.

---

## 19. Traps for the next engineer

From the root file, minus the ones already covered in sections 2 and 9.

1. **Never trust "the function is tested" as "the system uses it."** Seven
   components were dead code with green tests. Grep for call sites.
   `tests/test_wired_invariants.py` exists for exactly this.
2. **Small models cannot follow text output formats.** Both the action
   protocol and the deputy's verdict needed constrained decoding (a JSON
   schema via Ollama's `format`). The deputy silently denied **100%** of
   actions for want of it — which would have made a 7-hour benchmark report
   0% on every task and look like a suite that was too hard.
3. **Ollama defaults to a 4096-token context** regardless of the model, and
   truncates silently. Always send `num_ctx`.
4. **A `$0.00` budget must not mean "cannot run."** It meant that once, and
   every local task was born exhausted.
5. **Routing must not cost a model call.** A throughput probe fired a real
   completion, bypassed telemetry, and triggered a 58s cold load purely to
   make a routing decision.
6. **Sandbox path names matter.** Flattening `work/E01` to `E01` breaks every
   task whose instructions name the documented path.
7. **Verify the benchmark's own answers.** H27 was unsolvable as first
   generated — a correct solver found 13 bad rows against an expected 4,
   because the synthetic telemetry did not conserve energy. H30 needed its
   heliocentric-speed convention pinned in the objective for the same class
   of reason. `tests/test_bench_suite.py` recomputes both independently.
8. **`work/` lives under OneDrive**, whose sync client holds file handles.
   `shutil.rmtree` on a task workspace fails with `PermissionError [WinError
   5]` mid-run, which killed a noise-floor pass. The workspace reset deletes
   *files* and leaves directories for this reason. Excluding `work/` from
   sync would remove the whole class of problem.
9. **The venv is at `%USERPROFILE%\.alpha-test-1-venv`,** not beside the
   source — OneDrive deleted an in-folder `.venv` twice.

---

## 20. Glossary

**Artifact** — stored content, addressed by hash, referenced not pasted.

**Taint** — provenance label: `trusted` < `user` < `untrusted`. Combining
takes the maximum; it never decreases.

**Ratchet** — a revision is kept only if it scores strictly better.

**Discordant pair** — a paired trial where the two arms disagree. Only these
carry information in McNemar's test.

**Deputy / auto-review** — a second model that decides permission boundaries
so the agent does not stop for a human. Visible to the agent. Off in every
benchmark run.

**Monitor** — a silent watcher. **Never** visible to the agent.

**Stable prefix** — the byte-identical prompt opening that a provider caches.

**Two-touch** — approve a plan, receive a result, nothing in between.

**Drafting / eval split** — tasks a generator may see, versus tasks it may not.

**Harness version** — the definition of "what counts as a pass". Numbers from
different harness versions must never share a chart.

**`done_unannounced`** — the artifact is correct but the model never said so.
Counted separately from `done`, everywhere.

---

## 21. The claim to actually make

Not "10x cheaper" — that is a framework pitch nobody can check. The claim the
instrument is built to support is:

> **Skills that pass a paired-trial gate raise a local model's task success by
> X points at Y% of the cost of a frontier model, measured on N tasks.**

Report three ways — bare model, model + scaffold, full system — with date,
model version, and profile. State contamination caveats in the same paragraph
as the number, never a footnote.

Note what the harness tax (section 6) does to this claim. "Model + scaffold"
was assumed to be the *floor* a skill improves on. On the 3B it is measurably
**below** the bare model, so that middle arm is not a floor, it is a deficit.
Any lift a skill shows against the scaffolded arm has to be read against
that, and the bare-model arm stops being an optional third number.

**It might not work.** Self-generated skills average slightly negative in the
literature. The gate exists so that failing is visible instead of silent.
That is what makes it worth trying.
