# Handover

For a competent Python developer who has never seen this project. No prior context assumed.

**Structured against its own failure mode.** Stale documentation is a documented defect
class here — three instances, each found by running the thing the document described. So
every fact that changes weekly lives in **section D** and nowhere else with the command that
regenerates it, A/C/E are durable prose, and anything another document owns is pointed at
rather than reproduced. `tests/test_handover_is_current.py` fails when this file drifts.

# A — ORIENTATION

## A1. What this is

**The system.** An agent runtime. You give it an objective, it produces a plan, you
approve the plan once, and it works unattended until it reports back. It runs entirely on
local models through [Ollama](https://ollama.com): no API key, no network. There is a
hash-chained event log so a run can be audited and resumed, a capability gate that refuses
tool calls whose arguments came from untrusted sources, and a sandbox for model code.

**The question it was built to answer.** Published work reports that *human-written*
procedures ("skills") raise a model's task success by roughly +16 points, while
*self-generated* ones average about **−1.3** — slightly negative. So the interesting
problem is not generating skills; it is building an instrument honest enough to tell a
real improvement from noise. The paired trials, the statistical gate and the contamination
rules all exist for that. C5 explains the method from scratch.

**What was established, and it is not what was planned.** The original research question was
**retired unanswered**: no skill was ever generated and none ever went through the full gate.
The generator and its two invariants were deleted rather than left standing as a claim —
FINDINGS 1.27 says why and what would reopen it. Two results came instead:

1. **The scaffold degrades a model that can otherwise do the task.** The same 3.4B model,
   asked the same question, is right 5 of 5 prompted directly and 0 of 5 inside the harness,
   deterministically both ways. The cause is the output contract, not tools or context
   length, and the mechanism is *answer position*: a response that emits the number first
   has nothing left to reason with. It does **not** generalize across tasks or model sizes.
   FINDINGS 1.6, 1.7, 1.8.
2. **A catalogue of defect classes in the instrument itself**, each found by a method
   rather than by luck. That is C3, the most transferable thing in the repository.

## A2. Which reader are you

* **To use it:** A, B, then the security posture in **C4**. Stop there.
* **To continue it:** all of it, then **E**.
* **To evaluate it:** **A1**, **C4**, and `RELEASE.md`'s scorecard (E1 orients you).

# B — RUNNING IT

## B1. Prerequisites, and the traps

Windows, plus one Linux run in WSL2; macOS never run (`docs/PLATFORMS.md`); Python
3.11 or 3.12 (**not 3.13+**); Ollama ≥ 0.32.7; ~8 GB of disk for the two models. Every number here came from a machine with 15.5 GB of RAM doing CPU
inference, so wall-clock figures do not transfer. **Five traps, each costing someone a day:**
1. **`ANTHROPIC_API_KEY` must be unset.** `--profile local` refuses to start if any provider
   credential is visible — a stray key means a run labelled "local" might not be.
2. **`--suite` and `--tasks` are required arguments**, having lost their defaults when the
   aerospace suite was archived, so a command copied from an older document exits 2.
3. **`pip install -e .` cannot work at this path.** The directory name contains Greek
   characters and setuptools writes its `.pth` file in the system ANSI codepage; for the
   same reason `.cmd` launchers fail silently, since `cmd.exe` reads batch files in the OEM
   codepage. Use the `.ps1` scripts and set `PYTHONUTF8=1`; pytest gets
   `pythonpath = ["src"]` from `pyproject.toml`. Non-editable wheel installs are fine.
4. **`python -m build` fails here.** A `build` directory at the root, left by a wheel build, shadows the
   `build` module, and "No module named `build.__main__`" reads like a missing dependency.
   Use `pip wheel . --no-deps -w dist`.
5. **Two directories named `alpha-test-1` exist on this machine.** Only the one under
   `OneDrive\Desktop` is live; the other is a stale copy that must never be edited.

## B2. Quickstart

```powershell
# 1. environment. Venv goes OUTSIDE the project - OneDrive ate an in-folder
#    .venv twice. Ends by running the suite.
.\setup.ps1

# 2. models
ollama pull granite4.1:3b
ollama pull granite4.1:8b

# 3. the model server. LEAVE THIS WINDOW OPEN. Dedicated instance on 11435 with
#    its own model directory, so an existing Ollama on 11434 is untouched.
.\ollama-serve.ps1

# 4. one task end to end, scripted model - no network, no GPU
$env:PYTHONUTF8 = "1"
& "$env:USERPROFILE\.alpha-test-1-venv\Scripts\python.exe" demo.py
```

Step 4 must end close to this; if not, fix that first — it is the cheapest proof the
pipeline works, and it was dead for ten days:

```
gate refused append_to_outbox: append_to_outbox requires <=user arguments, args are untrusted
message actually sent to the address from the page: no
file written: True
chain verifies: True (first bad seq: None) - paid 9,091 tok - $0.0353 - cache hit n/a - 6 model calls
```

It planned, executed, **refused an instruction embedded in a web page it read**, and the
chain verifies. **Verification scope, narrower than it looks.** Every command here was run on **Windows, on
one machine, against a local Ollama**, on the date in section D. **POSIX has run twice, in
WSL2** (`docs/evidence/wsl-2026-09-25/`, `docs/evidence/wsl-2026-09-30/`): the first found
D54-D59, the second ran the whole suite green with them fixed. Bare-metal Linux has not run it, and macOS (`src/agent/platform/darwin.py`) has
never executed anywhere; the checklist in `docs/PLATFORMS.md` section 5 is for both.

## B3. What each command costs

| Command | Cost |
|---|---|
| `demo.py` | ~30 s, scripted model, no network |
| `python -m pytest tests -q` | ~1 min, in parallel by default, offline; `-n 0` for serial, ~4 min |
| `packaging/build.py` | ~4 min: the desktop app, windowed onedir, 62 MB. `--probe` builds the frozen sandbox gate |
| `bench/report.py`, `bench/coverage.py`, `bench/nextstep.py` | instant, read-only |
| `bench/ladder.py` | ~15 min for six rungs at 5 samples; `--rungs R0,R1` is ~4 min and answers "does the tax reproduce" on its own |
| `bench/noise.py --runs 1` | **~2.5 hours.** Loads a model, holds ~3.3 GB, takes the machine |
| `bench/run.py paired` | **~4 hours** |

Both long runs are resumable: each finished task is appended to a `.partial.jsonl` ledger
with an fsync, so a kill costs at most one task. Resuming needs the `order_seed` printed at
start — order is randomised and the seed is part of the resume signature, so without it the
run refuses rather than blending two orders, which is the guard working.

**Never run anything else during a timed run.** The cost axis is wall-clock seconds and
contention corrupts the only axis there is: the suite alone took 8 minutes under contention
instead of 1.5, and contention can manufacture a `budget_exhausted` result on a task that
would have finished. One measurement process at a time, enforced by an OS lock
(`src/agent/runlock.py`).

# C — WHAT YOU NEED TO KNOW

## C1. How the work was done

The method, and the part most worth stealing — practices to adopt on day one.
* **Run every new test against the defect it claims to catch** — put the defect back,
  confirm red, revert. In one phase 2 of 10 came back green against their own defect; in
  another, 3 of 10. A test that cannot fail is a comment with a runtime cost.
* **Prefer injecting a condition over waiting to observe it.** Stalls appeared about 3
  times in 60 tasks: waiting costs hours and proves nothing when it does not come.
* **When a check fires and the outcome is still wrong, verify the PATH from check to
  outcome, not the check.** Several defects here were a correct check reaching nothing.
* **Decompose values; do not inherit labels.** "Unstructured failure" survived two sessions
  as an assumption until someone factored the numbers and found the suite's declared trap.
* **Ask what a check would score for something that did the work correctly but behaved
  slightly unusually.** That found four instrument defects, each scoring something real
  that was not the deliverable.
* **Commit a prediction before measuring it**; criteria written afterwards are a
  description, not a test.
* **State what a result cannot support in the same paragraph as the result.** A caveat one
  paragraph away gets quoted without it.

## C2. The six design rules

In `INVARIANTS.md` with their instances. Not per-run invariants with a test that fires —
rules for the next person writing a control, each there because it was violated twice.

| | Rule | Instances |
|---|---|---|
| **DR1** | A control's scope must match what it protects — in WIDTH and in LIFETIME | eleven in one sweep |
| **DR2** | A default must not shadow the path it is a default for; a pre-set seam is a hard-coded path with a decoy branch beside it | the class, swept |
| **DR3** | A control must guard the BYTES, not one route to them | six file routes, then every SQLite route |
| **DR4** | Empty means nothing — "parsed nothing" and "matched nothing" are different facts | the scraper, the gate |
| **DR5** | A defect is never in one place; the sweep is part of the fix, not a follow-up | six, one of which cost ten days |
| **DR6** | An entry is closed in the change that fixes it, not in a later triage | two, found by a triage that read the code |

DR5 first: grep for the **shape** — the format spec, the call site, the field name — not
the symptom. A fix that closes one instance and schedules the sweep has been split.

## C3. The defect classes — hunt, do not wait

Each produced more than one instance, so the next is a search, not a surprise.
**Adjacency — the thing measured is adjacent to the thing claimed.** The largest class, and
it invalidates numbers instead of crashing. Four mechanisms: the grader was *reachable* by
the thing it graded; the workspace was *shared* between the two arms compared; the check ran
only inside the *announcement* branch, so work that finished without saying so scored zero;
a rung scored *step allocation*, not correctness. Found by C1's correct-but-unusual question.
**Expect the next wherever a score or threshold is added.**

**Scope mismatch (DR1) — a control stored at one granularity guarding something that lives at
another.** Two axes: *width*, a key shared with something else, such as a per-config anchor
guarding per-user memory; and *lifetime*, a holder that dies before the thing it holds state
about. The failure is not a missed detection but a **permanent false positive from ordinary
use**, which trains people to ignore the channel. **Expect the next wherever state is keyed.**

**Process — anything spawned outlives what spawned it.** A venv `python.exe` on Windows is a
trampoline, so killing the pid `Popen` returned leaves the real interpreter running; an
orphaned model worker held 5.9 GB and turned an 88 s call into 1499.8 s. Route every
termination through `kill_tree()` in `src/agent/proc.py`, and reap orphans before any timed
run. **Expect the next at any new stop path** — the most recent was a PowerShell verb calling
`Stop-Process` directly.

**Inert guarantees — a check that exists, is tested, and cannot fire.** An anti-gaming veto
needing data nothing recorded; a gate threshold arithmetically unreachable below n=160; a
contract field the model reads as a ceiling nothing enforces. Worse than a missing check,
because they read as protection. **Expect the next wherever a check's input is produced by
something that was never built.**

**Entry points — the least-tested code in a project.** A test imports what an entry point
*calls* instead of calling it, so coverage accumulates underneath the front door and never
touches it; the deeper the unit coverage, the more convincing the illusion. Two documented
front doors were dead for ten days, broken by one commit, with a green suite over both, and
the same session found the mirror image: two network-safety tests guarding a wrapper no
production path called. Both are invisible to a dead-code audit, which reads production call
sites — `main` is called by the operating system. FINDINGS 1.28.

> **Swept 2026-09-17: D39, the CLI's 25 subcommands — it contained nothing**, said plainly
> rather than left as a silence. **Expect the next wherever a test imports what an entry point calls.**

## C4. Security posture

Stated plainly so it can be evaluated rather than trusted.

**What the sandbox is:** advisory path and network confinement (writes to the workspace, reads
to it plus the interpreter, no sockets - loopback included), a **Windows Job Object**: memory, CPU
time and process count, and a wall-clock timeout. All three are real and tested.

**What it is not: a boundary against code that means harm.** It constrains a cooperative
program, not a hostile one: ctypes goes around both fences (the no-admin kernel fence is
AppContainer, a user decision: `docs/PROPOSALS.md` P1). File size is **not limited on Windows**
(`RLIMIT_FSIZE` is POSIX-only): on an attended machine, resource exhaustion, not a boundary.

**The capability gate — the one control that is a boundary — has never refused anything in
production.** It was consulted on all 614 tool calls in the measurement record and its
refusal path did not fire once, because nothing untrusted was ever passed to a restricted
tool. It refuses correctly in tests and in `demo.py`, but has never *had* to work.

**What would make it a wall:** a container, with the agent's filesystem view constructed
rather than constrained. **That was decided against** — Phase 6 withdrawn, 2026-09-16 —
because the Job Object carries most of the isolation benefit, the remaining gap is resource
exhaustion rather than a boundary, and the real cost is Docker becoming a runtime dependency
of the component that most needs to stay auditable. `docs/DEFERRED.md` D32 records this as
**known and accepted**, not deferred, and names what reopens it: untrusted code, or a
second user.

**So: do not run this against code you did not write, and do not run it unattended on a
machine you care about.** The threat model is a mistaken agent, not a hostile one.

## C5. How the measurement works

Read this before interpreting any number in the repository.

**A skill** is a short written procedure — at most three modules — prepended to the model's
prompt. **A paired trial** runs the *same task twice*, once with the skill loaded and once
without, both arms from an identical freshly rebuilt workspace. Pairing matters because tasks
differ enormously in difficulty: 30 tasks with a skill against 30 different tasks without
would mostly measure which tasks landed where.

**Only disagreements carry information.** Tasks both arms pass, or both fail, cancel out;
only the *discordant* pairs — skill passed and control failed (**b**), and the reverse
(**c**) — say anything. That is **McNemar's test**: under the null a discordant pair is
equally likely to fall in b or c, so the question is whether b exceeds c by more than chance
allows. It is a one-sided binomial test on b out of (b+c), needing roughly **75–80% of
disagreements to favour the skill** at any n.

**The gate promotes only if all four thresholds hold** (`config.local.yaml`, `gate:`): 15
paired trials, 10 discordant pairs, lift ≥ 5 points, p < 0.05. `min_discordant_pairs` is the
load-bearing one; `min_lift_pp` is **inert below n=160 at these settings and is not
protection** — C3's inert-guarantee class, inside the gate itself.

**Trials are counted per task, never per run** — two runs of one task share its difficulty,
so counting them separately inflates n and makes the p-value anti-conservative. **The eval
split is sacred:** a generator sees drafting tasks only, because a skill drafted from a task
and scored on it measures memorization. `--split eval` enforces the boundary.

**A positive control is a control, not a result.** The calibration skill answers one
question: *does the detector fire when a real effect exists?* A gate that rejects everything
and a gate that works look identical until it passes. Its lift is **not** evidence that
skills generalize, and its caveat belongs in the paragraph with its number.

## C6. A note on instructions

Four of the specifications driving this work contained errors caught by **measuring rather
than complying**: an ablation that measured necessity where it needed sufficiency; a premise
that a set of failures did not decompose, when factoring them showed it did; a claim that an
uncalibrated tokenizer had no live consequence, when the context ceiling budgets against it;
and an abort threshold that counted recovered faults together with lost data. Treat that as
an expectation, not an anecdote: **checking an instruction is part of following it, the
measurement wins, and the contradiction is written down beside the code defects.**

# D — STATE

**Everything volatile is here and nowhere else in this file**, each row with the command that
regenerates it. Verified 2026-10-01, against commit `4c39167`.

| Fact | Value | Regenerate with |
|---|---|---|
| Branch | `main`, the line of record, fast-forwarded to `iteration-3` (the unattended run of 2026-09-28/29, `docs/UNATTENDED-REPORT.md`) once its suite, frozen gate and audit passed; tag `g1-closed-2026-09-25` marks the measurement branch's tip; no remote | `git branch --show-current` |
| Written against | `4c39167`; the test asserts this is an ancestor of HEAD | `git log --oneline -1` |
| Harness version | **7**, bumped 2026-10-05: P5 (a graded program cannot write its own verdict), P6 (the ceiling uses a per-backend fit of the estimator), P3 (a follow-up sees its conversation). Numbers from 6 and earlier do not compare | `grep HARNESS_VERSION src/agent/__init__.py` |
| Toolset version | **6** | `grep TOOLSET_VERSION src/agent/__init__.py` |
| Test count | *deliberately not stated* | `python -m pytest tests -q`, and read the tail line. The skips are the live-credential tests, until D26's credentials exist |
| Open defects | *deliberately not stated* | the state table at the top of `docs/DEFERRED.md`; every entry also carries its state in its own heading |
| Phase program | complete; Phase 6 and Phase 12 both withdrawn | `RELEASE.md`, "ITERATION TWO: COMPLETE" |
| Running now | nothing | `Get-Process python, ollama -ErrorAction SilentlyContinue` |
| Uncommitted | nothing | `git status --porcelain` |

# E — WHAT COMES NEXT

## E1. Pointers

Each owns its subject; nothing here is reproduced above.
* **`docs/FINDINGS.md`** — the results in evidential order, each with what it does *not* show
  and what would change the author's mind, written in advance. Section 5 is a reading order
  by evidential strength; start there.
* **`docs/FINDINGS.md` 1.25** — the inventory of **what has never run for real**, taken
  against the measurement record rather than the test suite, with what fails silently if each
  path is wrong. Its method is the reusable part: a unit test proves a function works and
  says nothing about whether the system ever called it.
* **`RELEASE.md`** — the audited goal-by-goal scorecard, G1 to G8, with what each would take
  to finish. The honest answer to "is this done".
* **`docs/SPEC-DELTA.md`** — every place the specification is now known to be wrong, with the
  correct version and whether the divergence was deliberate, an oversight or a workaround.
  Holds instruction-level errors too (C6).
* **`spec/CORRECTIONS.md`** — errata for the original design documents: a model that does not
  exist, the wrong tokenizer, an API parameter that now returns 400. **Read it before
  `spec/BUILD-SPEC.md`**, not after.
* **`INVARIANTS.md`** — the pre-commit checklist, each item naming the test that catches its
  violation, plus C2's design rules. **`docs/DEFERRED.md`** — the open defects, each heading
  carrying its own state.
* **`docs/PLATFORMS.md`** — what runs on which OS (only Windows has), the POSIX defects fixed
  from reading, and the checklist that verifies a new machine. **The desktop app** is
  `src/agent/desktop.py`, built by `packaging/build.py`; `docs/evidence/desktop-app-2026-09-25.md`
  is what it was shown to do frozen.
* **`docs/evidence/`** — the raw records behind every number, including superseded and
  aborted runs, each labelled with its cause. `runs/` is gitignored, so anything that
  matters is copied here before a long job.

## E2. The open items

| | What closes it |
|---|---|
| **D2** — the cause of the pathological model call is unknown | Separating llama.cpp KV-cache behaviour, OS paging, thermal throttling and I/O contention. Detection and recovery already exist, so no measurement is at risk; this is diagnosis |
| **D6** — the in-loop token estimator is uncalibrated | **Measured 2026-09-30** from the G1 records: it undercounts ~26% (server = 1.108 x est + 193), so the ceiling fires late. Correcting it moves compaction: `docs/PROPOSALS.md` P6, a HARNESS_VERSION matter |
| **D16** — per-backend context windows are unreachable over the OpenAI protocol | The fit above exists for ONE tokenizer (granite via Ollama); a check on another backend's `prompt_tokens` still has an unknown false-alarm rate. Needs a live model per backend |
| **D26** — the Telegram transport and the Anthropic adapter have never made a live call | A bot token and an API key. Both tests are written and skip cleanly; `README.md` gives the one command per credential and what a pass looks like |
| **D27** — the bot has no queue | The lock half is fixed (claim-then-execute receipts). What is left is the Telegram loop's queue and acknowledgement, with D26: that transport has never made a live call |
| **D33** — tier 1 sits before the tool schemas on this template | A `HARNESS_VERSION` bump, which is a stop. Its criterion — a boundary rebuild more than once per task — is **measured at zero** as of 2026-09-17 (`bench/inventory.py`): no session has ever rotated. Re-refused, not unexamined |

Also open: D52 (tool schemas' per-step tokens, P2) and **D79**, a graded program can print its own
verdict (P5); everything else from the audits and the WSL2 runs is closed. **D26** is the user's. D32's acceptance is in C4.

## E3. Next steps, in order

**Estimates are mine, from my own reading of the code. Every prior estimate here ran
optimistic, so where I am guessing I say so.** They stand as written with what has since been
done marked beside them: a prediction edited afterwards is not one.
**(a)-(e) are done. Next, 2026-09-29:** what waits for the user, top of `docs/UNATTENDED-REPORT.md`; then `docs/PLATFORMS.md` section 5; then D54-D57.

**(a) Feature-flag the efficiency mechanisms, then run G1.** First, because it closes the
headline goal — and *the reason it was never testable is a spec error, not an implementation
gap*: the four mechanisms were **structural in `src/agent/execute/loop.py` with no
switches**, so no configuration represented "without compaction". No API key needed — token
counts come from the model server. *My estimate:* the first three are call sites with a clear
"off" meaning, **1–2 days including tests**; code-as-action is **a design task of 2+ days, and
I am uncertain**; the comparison is four suite passes, **a day of wall clock**.
**Switches done 2026-09-17** (`mechanisms:`); **the RUN is not**, and code-as-action is still the design question.

**(b) Subprocess smoke tests on every entry point and all 25 subcommands (D39).** *My
estimate:* **about a day, not the half-day the entry says.** The scratch-database fixture
is the work — a task, a plan, an approval, a memory row and a skill — 2–3 hours before the
first subcommand is touched. Then budget for what it finds: this layer has produced a
defect every time anyone looked, and under DR5 a defect found is a sweep run. **Done
2026-09-17**, and that last sentence was wrong: it found nothing.

**(c) The small fixes.** D38's bump batched with D33's tier-1 move: **a few hours of code**,
but the cost is the decision, not the code — the bump makes every published number
incomparable with every later one. D24 is **1–2 hours**; D27 is a decision rather than work.
**D24 done 2026-09-17** — not 1–2 hours of typing but ten minutes of running it: the entry's
"it fails loudly" was false and the defect was corrupting (SPEC-DELTA C68).

**(d) Use the system for real work, then re-run the 1.25 inventory.** Costs nothing and
closes the most, because almost nothing here has run in production for the simple reason
that nobody has used it; every row of that inventory that turns green turns green for free.
*My estimate:* the re-run is **an hour** of SQL. The using is not a task, it is a habit.
**Done 2026-09-17:** the SQL is `python bench/inventory.py` — 9 of 36 paths have run.

**(e) The frozen-app fix, if a desktop app is ever wanted.** Dormant — nothing is frozen
today. *My estimate:* **a day, and I am uncertain**, because nobody has pointed a freezer at
this tree, and the failure modes (package data, the subprocess trampoline, the sandbox's own
interpreter path) only show up then. **Done 2026-09-25**: about a day for D13 itself, and the
freeze found two defects only a freeze shows (SPEC-DELTA C72, `desktop-app` branch).

## E4. Open questions

1. **Why can the 8B not terminate?** It writes correct answers and never emits the
   completion tag — across 8 tasks it never emitted one at all, while the 3B managed both
   protocols with byte-identical config. Under a two-touch model an agent that never says
   "done" hangs: an operational hazard, not a benchmark artifact.
2. **Is this task family right at all?** The design says a small local model's scope is
   "summarizing, sorting, extracting"; the benchmark asks for multi-step numerical
   reasoning *and* program synthesis *and* file I/O. The specification contradicts itself,
   and the harness tax may be the consequence.
3. **Is `done_unannounced` a pass?** It counts as one because the artifact is correct; the
   opposing view — failing to terminate is a real failure — is defensible, which is why both
   counts are reported separately everywhere.
4. **Does prefix *content* move pass rates, or only *structure*?** FINDINGS 8.1. And **is
   `--profile local` answering a different question than the spec asks?** FINDINGS 8.2.
5. **What is the fifth adjacency defect?** Four were found by one question (C1); nobody has
   asked it of the newest checks.
