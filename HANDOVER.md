# alpha test 1 — Handover

**Everything a new engineer needs to take this over. Read this file alone.**

Version 0.1.0 · 19 commits · 183 tests passing · built from `spec/BUILD-SPEC.md`

---

## 0. What this is, in one page

A hybrid local/cloud AI agent that tries to answer one research question:

> **Do written procedures ("skills") make a cheap local model competent enough
> to do most of the work — and can that be measured rather than asserted?**

The published evidence says human-written skills lift task success by roughly
+16 points, while *self-generated* skills average about **−1 point**. Negative.
So the interesting engineering problem is not "generate skills" — it is
**building an instrument honest enough to tell a real lift from noise**, then
seeing whether a careful generation process can beat that baseline.

Everything in the codebase exists to keep that measurement honest. That is why
there is a hash-chained event log, a capability gate, a paired-trial
statistical gate, and a benchmark whose verifiers check answers rather than
shapes.

**Current status:** the machinery is built and works end to end on local
models with no API key. The instrument has been validated once at small scale
(3 tasks: 0/3 baseline → 3/3 with the calibration skill). The full statistical
run has **not** been done — that is the next step and it is measured in hours,
not minutes.

### The two-touch interaction model

```
   You give it an objective
            ↓
   It plans (read-only — writes are physically disabled)
            ↓
   ══ TOUCH 1: approve / edit / reject the plan ══
            ↓
   It executes unattended
      ├─ routine action              → runs
      ├─ permission boundary         → a second model (the "deputy") decides
      ├─ trusted-tier action         → PARKS for you
      └─ a silent monitor watches throughout
            ↓
   ══ TOUCH 2: the result ══
```

Anything that adds a third mandatory touch is a bug.

---

## 1. Getting it running (15 minutes)

### Prerequisites
- Windows (this build is Windows-tested; the code is cross-platform but only
  Windows has been exercised)
- Python 3.11 or 3.12 — **not 3.13+**, package gaps
- [Ollama](https://ollama.com) ≥ 0.32.7
- Git

### Setup

```powershell
cd "C:\Users\char1\OneDrive\Desktop\Βουκεφάλας\alpha-test-1"
.\setup.ps1
```

That creates the virtualenv, installs dependencies, and runs the test suite.
You should see `183 passed`.

### Start the model server (leave this window open)

```powershell
.\ollama-serve.ps1
```

This runs a **dedicated Ollama instance on port 11435** with its models in
`../ollama-models`. Your existing Ollama on 11434 is untouched. If the window
closes, every local model call fails with a connection refusal.

### Run something

```powershell
# the whole pipeline with a scripted model — no network, no models needed
..\..\..\.alpha-test-1-venv\Scripts\python.exe demo.py

# a real task on the local model
.\alpha.ps1 --config config.local.yaml new "Read work/notes.txt and tell me what it says." --scope work
.\alpha.ps1 --config config.local.yaml run t_xxxxxxxx

# the web console
.\alpha.ps1 --config config.local.yaml serve      # → http://127.0.0.1:8901
```

### Two environment quirks that will bite you

1. **`pip install -e .` does not work at this path.** The folder name contains
   Greek characters, and setuptools writes its `.pth` file using the system
   ANSI codepage (cp949 on the dev machine), which cannot encode `ά`. Use the
   `alpha.ps1` launcher; pytest gets `pythonpath = ["src"]` from
   `pyproject.toml`. If you move the project to an ASCII path, normal installs
   work.
2. **The venv lives outside the project** at `%USERPROFILE%\.alpha-test-1-venv`.
   OneDrive deleted an in-folder `.venv` twice; the multi-GB model blobs beside
   it survived. The launchers check `.\.venv` first, so moving it back needs no
   code change.

---

## 2. Where everything is

```
Βουκεφάλας/
├── ollama-models/          7 GB — granite4.1:3b and :8b (git-ignored)
└── alpha-test-1/
    ├── HANDOVER.md         ← you are here
    ├── README.md           usage detail
    ├── INVARIANTS.md       the pre-commit checklist. Read before changing anything.
    ├── spec/               the ORIGINAL BUILD-SPEC.md and EXPLAINER.md
    ├── setup.ps1           one-command environment
    ├── alpha.ps1/.cmd      CLI launcher
    ├── ollama-serve.ps1    dedicated model server on :11435
    ├── config.yaml         mixed profile (cloud + local) — UNVERIFIED cloud path
    ├── config.local.yaml   local-only profile — this is the one that works
    ├── policy.md           the deputy's rule book (also editable in the console)
    ├── demo.py             end-to-end with a scripted model, no network
    ├── src/agent/          the system (64 files, see §3)
    ├── tests/              183 tests (19 files, see §6)
    ├── bench/              benchmark harness + the aero_calc suite
    ├── examples/skills/    the hand-written calibration skill
    ├── docs/               published benchmark report
    └── runs/               run records, git-ignored
```

### Reading order, if you are picking this up

1. **`HANDOVER.md`** — this file. What exists, how to run it, what to do next.
2. **`spec/CORRECTIONS.md`** — **read before opening the spec.** The original
   contains factually wrong instructions (a model that does not exist, the
   wrong tokenizer, an API parameter that now 400s, an inverted comparison, a
   config that violates its own invariant). This lists every one, plus what is
   merely incomplete and what is still valid.
3. **`spec/BUILD-SPEC.md`** and **`spec/EXPLAINER.md`** — the reasoning behind
   every design decision, preserved unedited. Read when you need to know *why*
   an invariant exists before changing code near it. The reasoning is sound;
   the specific commands and constants are not all reliable.
4. **`INVARIANTS.md`** — the pre-commit checklist.

Send all four to a coding agent, in that order. The spec carries the evidence
that justifies the invariants — without it an agent follows the rules without
knowing which are load-bearing, and will "simplify" one of them.

---

## 3. Architecture

The system is 13 milestones (M1–M13) from the spec. Build order is not
arbitrary — several later components are unsafe or unmeasurable without
earlier ones.

```
                    ┌──────────────────────────────┐
   console (:8901)  │  M12 control plane (FastAPI) │  CLI (alpha.ps1)
   web UI ─────────►│  + M13 plan / deputy         │◄──────────
                    └───────────────┬──────────────┘
                                    │
                    ┌───────────────▼──────────────┐
                    │  M4 the loop (execute/loop)  │
                    │  plan → act → verify → ratchet│
                    └───┬────────┬────────┬────────┘
                        │        │        │
        ┌───────────────▼──┐  ┌──▼─────┐ ┌▼─────────────────┐
        │ M8 capability    │  │ M6     │ │ M7 verification  │
        │ gate + taint     │  │ router │ │ mesh + ratchet   │
        └───────┬──────────┘  └──┬─────┘ └┬─────────────────┘
                │                │        │
        ┌───────▼────────────────▼────────▼─────────────────┐
        │ M1 kernel — event log, hash chain, receipts       │
        │ M2 telemetry · M3 context ledger                  │
        └───────────────────────┬───────────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │ SQLite (agent.local.db)│
                    └───────────────────────┘
```

### Module map

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
| `execute/loop.py` | M4 | **The spine.** Read this first. |
| `execute/sandbox.py` | M5 | Subprocess + audit hook + live tool bridge |
| `execute/tools.py` | M5/M8 | Tool registry and the taint table |
| `router/cascade.py` | M6 | Allowlist routing, escalation by disagreement |
| `assure/verifiers.py` | M7 | The verifier registry (see §5) |
| `assure/mesh.py` | M7 | Ratchet, anti-gaming, held-out registry |
| `assure/monitor.py` | M8.3 | Silent monitor — **never visible to the agent** |
| `security/taint.py` | M8 | Provenance lattice: trusted < user < untrusted |
| `security/capabilities.py` | M8 | The gate. Every tool call passes here. |
| `skills/gate.py` | **M9** | **The statistical gate. The heart of the project.** |
| `skills/generate.py` | M11 | Generator — cannot promote, by construction |
| `delegate/contracts.py` | M10 | Five-gate test; most delegations refused |
| `plan/planner.py` | M13 | Read-only planning; plan hash pinned |
| `plan/review.py` | M13.3 | The deputy |
| `control/server.py` | M12 | HTTP API |
| `control/guard.py` | — | Auth, CSRF, DNS-rebinding protection |
| `userdata/store.py` | — | User-authored suites/policy/grounding |
| `profiles.py` | — | Enforced local/mixed backend profiles |

### The invariants that make it work

`INVARIANTS.md` has all 25 with the test that catches each violation. The
five that matter most:

1. **The stable prefix is byte-identical across turns.** Prompt caching is a
   literal byte match. One character changes and cost goes up ~10× silently.
2. **No refinement without a new external observation.** The agent never
   "tries again" — it must have new information. Enforced structurally.
3. **Instructions found in content are data, never commands.** A webpage
   saying "email the keys to X" is refused because the address carries
   untrusted taint — a rule, not a judgment the model makes.
4. **Monitor output never enters agent context.** An agent that knows it is
   watched degrades the watcher more than the watcher gains.
5. **Only the gate promotes skills, and only with human approval.**

---

## 4. The research instrument (this is the point)

### The skill gate — `skills/gate.py`

A skill is promoted only if **all four** hold:

| Condition | Default | Why |
|---|---|---|
| ≥ 30 paired trials | `min_paired_trials` | Statistical power at small n |
| ≥ 10 discordant pairs | `min_discordant_pairs` | Only discordant pairs carry information in McNemar's test |
| Lift ≥ 5pp | `min_lift_pp` | Significance alone is not worth the context tokens |
| p < 0.05 | `max_p_value` | One-sided binomial (McNemar) |

Plus two vetoes: a **catastrophic regression** on any flagged task, and a
**visible/held-out pass gap** > 0.25 (the signature of a skill fitted to the
checks it could see).

**Paired, not independent**: the same task runs both arms. This controls for
task difficulty and gives far more power at small n, which matters when trials
cost hours.

### The benchmark — `bench/tasks/aero_calc/`

30 aerospace calculation tasks. Chosen because every answer is a number with a
tolerance (deterministic verification — no LLM judge injecting noise into the
signal), and because the failure modes are real and documented:

| Trap | Looks like |
|---|---|
| Altitude vs orbital radius | Forgetting to add body radius |
| Wrong body's μ or R | Earth constants on a Mars problem |
| Isp seconds vs exhaust velocity | Missing the g₀ factor |
| Degrees vs radians | Trig on unconverted angles |
| Given units ≠ SI | km/h, km, hours |

**Two splits, and the separation is load-bearing:**
- `drafting/` (15) — a skill generator may look at these
- `eval/` (15) — **never** enters a drafting prompt or any log a generator
  reads. A skill drafted from a task it is later scored on manufactures a lift
  that is not there.

The suite is **generated from one table** (`bench/suites/aero_calc.py`), not
120 hand-maintained files. Change a tolerance in one place and regenerate:

```powershell
python bench\suites\aero_calc.py --out bench\tasks\aero_calc   # write files
python bench\suites\aero_calc.py --load --config config.local.yaml   # into the DB
```

All 30 expected answers were independently recomputed and verified.

### The calibration skill — `examples/skills/aero_units_discipline.md`

Three modules targeting exactly the traps above. **Run this through the gate
before any generated skill.** If the gate cannot detect *this*, it cannot
detect anything, and every downstream rejection is uninterpretable.

| Calibration result | Meaning | Next |
|---|---|---|
| Promoted, lift > 10pp | Instrument works | Proceed to generated skills |
| Promoted, lift 5–10pp | Works, low power | More tasks, or a sharper skill |
| Rejected, few discordant | Suite not sensitive | Rebalance toward ~50% |
| Rejected, many discordant, wrong direction | The skill genuinely hurts | Investigate — that is interesting |

**A gate that rejects everything and a gate that works are indistinguishable
until the calibration skill passes.** Do not accept "expect rejection, that's
the gate working" as sufficient.

---

## 5. Verifiers — what "passed" means

In `assure/verifiers.py`. **The choice of verifier decides whether your
benchmark measures anything.**

| Verifier | Checks | Use for |
|---|---|---|
| `verify_program` | Runs the task's own `verify.py` against files on disk | **The eval suite.** Strongest. |
| `expected_value` | Answer equals a known value (`x-expected`) | Numeric/string answers |
| `contains_all` | Required substrings survived (`x-contains`) | Prose with checkable facts |
| `execution` | Code runs, exit 0 | Code-writing tasks |
| `json_schema` | **Shape only** | Almost never in an eval suite |
| `always_pass` | Nothing | Refused by the suite validator |

**The trap to avoid:** `json_schema` accepts `{"total": 0}` for an invoice
totalling 4825. A suite scored that way reports a high pass rate that means
nothing — the same failure an audit found in ~59% of SWE-bench Verified's
hardest tasks. The console shows a running count of how many of your tasks
cannot catch a wrong answer.

---

## 6. Testing

```powershell
$env:PYTHONUTF8="1"
..\..\..\.alpha-test-1-venv\Scripts\python.exe -m pytest tests -q
```

183 tests, all offline — no API key, no GPU, no network. A `mock` backend
replays scripted turns so the whole loop is exercised without spending
anything.

| File | Covers |
|---|---|
| `test_m1_kernel.py` | Chain tampering, real SIGKILL + resume, concurrency |
| `test_m3_ledger.py` | Prefix stability, compaction, taint-safe cache |
| `test_m4_loop.py` | End-to-end, budget stops, constrained decoding |
| `test_m5_action.py` | Sandbox escape, tool bridge, turn collapse |
| `test_m7_assure.py` | Ratchet, observation invariant, held-out leak |
| `test_m8_security.py` | **Injection blocked end to end**, monitor isolation |
| `test_m9_gate.py` | Gate calibration, contamination, catastrophic veto |
| `test_m12_serve.py` | HTTP, auth, CSRF, DNS rebinding, concurrency |
| `test_wired_invariants.py` | That the runtime actually *calls* the machinery |
| `test_routing_costs_nothing.py` | Routing makes no model calls |

**`test_wired_invariants.py` deserves special mention.** An audit found seven
pieces of spec machinery with passing unit tests and **zero call sites** in the
running agent. A test that a function works is not a test that the system uses
it. If you add a component, add a test that the runtime calls it.

---

## 7. What is complete

- **M1–M13 implemented**, with the spec's own acceptance tests
- **Runs end to end with no API key** — plan, gate, deputy, receipts, hash
  chain, telemetry, all on local models
- **Web console** — suites, policy, grounding, skills, runs, all authored in a
  browser rather than by editing files
- **Auth on the control plane** — bearer token, Origin allow-list, DNS-rebinding
  protection; off-loopback binding needs `ALPHA_ALLOW_REMOTE=1`
- **Enforced backend profiles** — `--profile local` refuses cloud backends *and*
  provider credentials, and the profile is recorded in every run record
- **The 30-task aero_calc suite**, answers independently verified
- **Correctness verifiers** — `expected_value`, `contains_all`, `verify_program`
- **The instrument validated once at small scale** — 3 tasks, 0/3 baseline →
  3/3 with the calibration skill, 3/3 discordant in the skill's favour

### Measured facts about this hardware (CPU-only, 15.5 GB RAM)

| | |
|---|---|
| `granite4.1:3b` | ~2–3 tok/s warm |
| `granite4.1:8b` | ~0.7 tok/s |
| Cold model load | ~58s (hence `keep_alive: 30m`) |
| Typical easy task | 100–270s |
| Cost | $0.0000 — everything local |

---

## 8. What is incomplete — the honest list

### Blocking the research claim

1. **The full noise floor has never run.** 30 tasks × 3 baseline runs ≈ 7h.
   Classify each task 3/3, 0/3, or flaky. **If more than 8 of 30 are flaky,
   stop and tighten task specs** — a lift measured against that much noise
   cannot be interpreted.
   ```powershell
   python bench\noise.py --config config.local.yaml --profile local --split eval --runs 3
   ```
2. **The calibration skill has never been through the full gate.** 30 paired
   trials ≈ 5h, on the **eval split only**.
3. **No generated skill has ever been produced or evaluated.** M11 works but
   needs grounding notes; expect a 10–30% promotion rate if the instrument is
   healthy.

### Known gaps

4. **The Anthropic adapter is UNVERIFIED.** Written, unit-tested, **never
   exercised against the live API**. Do not describe it as working. Marked in
   its module docstring. To verify: set `ANTHROPIC_API_KEY`, run one trivial
   task with `--profile mixed`.
5. **Single worker only.** The hash-chain lock is process-wide; a second
   uvicorn worker could fork the chain. The correct fix is claim-then-execute
   on the receipt primary key — deliberately not half-done.
6. **Multi-user is scaffolded, not built.** Every table carries `owner`
   (defaults `'local'`) and user-content queries are owner-scoped, so adding
   accounts is wiring an identity provider, not a migration.
7. **No TUI.** The CLI and web console cover it.
8. **`H30b` disagreement.** The suite expects 2054.1 for Mars capture Δv; an
   independent computation gives **2080.2** (1.27% apart). It passes at
   tol 2%, but a *correct* answer scores 0.37 on the continuous scale, so the
   ratchet would rank right answers as mediocre. **Decide which number is
   right before running the gate.**
9. **Baseline is 0% on easy tasks**, where the suite design predicted ~80%.
   With the calibration skill it is 100%. For McNemar this is maximum
   discordance (ideal), but if medium/hard are also 0% with *and* without the
   skill, there will be no signal. The noise floor will tell you.

---

## 9. The next three things to do

**1. Settle H30b** (5 minutes). Recompute the Mars capture Δv and fix either
the expected value or the tolerance. Everything downstream inherits this.

**2. Run the eval-split noise floor** (~7h, overnight).
```powershell
python bench\noise.py --config config.local.yaml --profile local --split eval --runs 3
```
Read `runs/noise-floor.json`. Check: how many flaky? Is the mean in the
35–65% band? If >8 flaky, tighten specs before going further.

**3. Run the calibration skill through the full gate** (~5h).
```powershell
.\alpha.ps1 --config config.local.yaml skills --add examples\skills\aero_units_discipline.md --name aero_units_discipline --when "aerospace calculation with units, body constants, or Isp"
python bench\run.py paired <skill_id> --suite aero_calc --config config.local.yaml
.\alpha.ps1 --config config.local.yaml skills --gate <skill_id>
```
Interpret with the table in §4. **If it is rejected, the instrument is broken
and every later rejection is meaningless. Fix that before anything else.**

A cheap sighting shot first — 15 minutes instead of 5 hours:
```powershell
python bench\noise.py --config config.local.yaml --profile local --runs 1 --limit 3
python bench\noise.py --config config.local.yaml --profile local --runs 1 --limit 3 --skill aero_units_discipline
```

---

## 10. Traps for the next engineer

Things that cost real debugging time here. Each is now guarded by a test, but
the reasoning is worth carrying.

1. **Never trust "the function is tested" as "the system uses it."** Seven
   components were dead code with green tests. Grep for call sites.
2. **Small models cannot follow text output formats.** Both the action
   protocol and the deputy's verdict needed **constrained decoding** (JSON
   schema via Ollama's `format`). The deputy silently denied *100%* of
   actions for want of this — which would have made a 7-hour benchmark report
   0% on every task and look like a suite that was too hard.
3. **Ollama defaults to a 4096-token context** regardless of the model, and
   truncates silently. Always send `num_ctx`.
4. **A `$0.00` budget must not mean "cannot run."** It meant that once, and
   every local task was born exhausted.
5. **Routing must not cost a model call.** A throughput probe fired a real
   completion, bypassed telemetry, and triggered a 58s cold load to make a
   routing decision.
6. **Sandbox path names matter.** Flattening `work/E01` to `E01` breaks every
   task whose instructions name the documented path.
7. **Verify the benchmark's own answers.** H27 was unsolvable as first
   generated — a correct solver found 13 bad rows against an expected 4,
   because the synthetic telemetry did not conserve energy.
8. **`.cmd` files cannot carry non-ASCII paths.** `cmd.exe` reads batch files
   in the OEM codepage and `cd /d` fails silently. Use `.ps1`.

---

## 11. Glossary

**Artifact** — stored content, addressed by hash, referenced not pasted.
**Taint** — provenance label: `trusted` < `user` < `untrusted`. Combining takes
the maximum; it never decreases.
**Ratchet** — a revision is kept only if it scores strictly better.
**Discordant pair** — a paired trial where the arms disagree. Only these carry
information in McNemar's test.
**Deputy / auto-review** — a second model that decides permission boundaries so
the agent does not stop for a human. Visible to the agent.
**Monitor** — a silent watcher. **Never** visible to the agent.
**Stable prefix** — the byte-identical prompt opening the provider caches.
**Two-touch** — approve a plan, receive a result, nothing in between.
**Drafting / eval split** — tasks a generator may see, versus tasks it may not.

---

## 12. The claim to actually make

Not "10x cheaper." That is a framework pitch nobody can check. The claim the
instrument is built to support is:

> **Skills that pass a paired-trial gate raise a local model's task success by
> X points at Y% of the cost of a frontier model, measured on N tasks.**

Report three ways — bare model, model + scaffold, full system — with date,
model version, and profile. State contamination caveats in the same paragraph
as the number, never a footnote.

**It might not work.** Self-generated skills average slightly negative in the
literature. The gate exists so that failing is visible instead of silent. That
is what makes it worth trying.
