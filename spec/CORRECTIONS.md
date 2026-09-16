# Corrections to the original specification

**Read this before acting on anything in `BUILD-SPEC.md` or `EXPLAINER.md`.**

The originals are preserved unedited — they are the record of what was asked
and the reasoning behind every invariant, which is still worth having. But
building the system surfaced things in them that are **factually wrong**, and
an agent that follows those instructions literally will waste hours or, worse,
silently break a guarantee.

Three categories below:

- **§1 Wrong** — do not do this. Corrected in the implementation.
- **§2 Incomplete** — not wrong, but missing something that matters in practice.
- **§3 Still valid** — explicitly NOT corrections. Follow the spec here.

Every item names the file that carries the fix, so you can read the working
version rather than re-deriving it.

---

## 1. Factually wrong — do not follow

### 1.1 `muse-glimmer:3b` does not exist

**Spec says** (§0.1, §0.1b): use Muse Glimmer 30B on desktop and "the same
family's 3-billion-parameter version" on a laptop.

**Reality:** all 15 Muse Glimmer tags in the Ollama registry are 30B. The "3B
assistant model" in the release notes is `Muse-Glimmer-30B-assistant`, a
**DFlash block-diffusion speculative-decoding drafter** — not a chat model. It
exists to accelerate the 30B and needs the 30B loaded beside it. It cannot
serve a laptop tier.

The 30B itself is real, but its smallest quantization is **17 GB** and will not
load on a 15.5 GB machine.

**What was done instead:** both local tiers are **Granite 4.1** —
`granite4.1:8b` (5.3 GB) and `granite4.1:3b` (2.1 GB). This satisfies §0.1's
"prefer one model family across all your machines" rule *better* than the
original pairing did: one chat template, one tool-call syntax, one code path.

Restore `muse-glimmer:30b` as the desktop tier if you move to a 24–32 GB GPU
machine. See `config.local.yaml`.

### 1.2 `tiktoken` is the wrong tokenizer

**Spec says** (§0.4): `tiktoken>=0.7  # token counting`.

**Reality:** tiktoken is OpenAI's tokenizer. It undercounts Claude tokens by
roughly 15–20% on prose and considerably more on code, so every cost figure
derived from it would be quietly wrong — which defeats the entire point of M2.

**Fix:** `src/agent/tokens.py`. A fast local estimator for in-loop budgeting,
and the provider's own `count_tokens` endpoint for calibration and for
anything published. Never report a headline token number from the estimator
without saying so.

### 1.3 `temperature` is rejected by current models

**Spec says** (M4.2): pass `temperature=temperature` to `messages.create`.

**Reality:** Opus 5 / Sonnet 5 / Opus 4.7+ reject `temperature` with a **400**,
not a warning. Extended thinking is also configured differently now:
`{"type": "adaptive"}` with depth set by `output_config.effort`;
`budget_tokens` is gone.

**Fix:** `src/agent/models/anthropic.py` omits `temperature` unless the
backend config says the model accepts it. Model IDs updated
(`claude-sonnet-4-6` → `claude-opus-5` / `claude-sonnet-5`), which the spec
itself invited: "verify current model names before use."

### 1.4 The semantic-cache taint comparison is inverted

**Spec says** (M3.5): serve a cache entry when
`Taint(row[1]).level >= taint.level`.

**Reality:** that hands untrusted-derived values to trusted callers — exactly
the laundering the spec's **own acceptance test** forbids ("store an untrusted
result, request it in a trusted context, assert miss").

**Fix:** the comparison is `<=`. `src/agent/ledger/semantic_cache.py`.
The test expressed the intent; the snippet did not.

### 1.5 `agreement >= 0.67` never accepts 2 of 3

**Spec says** (M6.3): `if agreement >= 0.67:  # 2 of 3 agree`.

**Reality:** 2/3 = 0.6667, which is **less than** 0.67. The literal constant
escalates every 2-of-3 agreement it was written to accept — silently doubling
cost on the path meant to save it.

**Fix:** `DEFAULT_AGREEMENT = 2 / 3` in `src/agent/router/cascade.py`.

### 1.6 The exact-string regex misses relative paths

**Spec says** (M3.3): `r"(?:/[\w.\-]+)+"` for POSIX paths.

**Reality:** that only matches *absolute* paths, so `src/parser.py` survives
compaction as `/parser.py` — violating the spec's own rule 3, "preserve exact
strings character-for-character."

**Fix:** `src/agent/ledger/compact.py` matches relative paths too.

### 1.7 The shipped config violates invariant 14

**Spec says** (§0.5): `auto_review_backend: "cloud"`, while `local_allowlist`
excludes `step` — so the executor is *also* `cloud`.

**Reality:** M13.3's anti-collusion assertion (`reviewer != executor`) fires on
the first review the system ever performs. The shipped config cannot run.

**Fix:** a separate `review` backend entry on a different model. See both
config files.

### 1.8 The M5.3 sandbox cannot pass its own M5 test

**Spec says** (M5.3): a subprocess with `resource` rlimits, and (M5 acceptance)
"code attempting to write `anchor_path` fails, and the anchor file is
byte-identical afterward."

**Reality:** a subprocess with rlimits does not stop
`Path(anchor_path).write_text(...)`. The spec's code cannot satisfy the spec's
test. `resource` and `preexec_fn` are also POSIX-only.

**Fix:** `src/agent/execute/sandbox.py` adds an audit hook inside the child
that confines writes to scope, and is cross-platform. Still honest about what
it is: a guard against mistakes, not against an adversary with code execution.

### 1.9 The schema omits tables M12/M13 require

**Spec says** (§1.1): the schema. **Reality:** it has no `approvals`,
`review_decisions`, `plans`, or `deviations` tables, all of which M12/M13
reference. Added in `src/agent/kernel/db.py`.

### 1.10 `context_fill_ratio` against a 1M window is meaningless

**Spec says** (§0.5): `context_fill_ratio: 0.28` with `context_window: 200000`.

**Reality:** fine at 200k. But follow the spec's own "verify current model
names" advice onto a 1M-context model and the ceiling becomes 280k tokens,
which is not a ceiling at all. M3.4's whole point is that usable context sits
well below the advertised number.

**Fix:** a separate `usable_context_window` key that M3.4 budgets against.

### 1.11 Leftover model name in the M9.3 example

`provenance.json` shows `"drafted_by": "local:qwen3-8b"` while §0.1 mandates
Muse Glimmer. Cosmetic, but it signals the spec was assembled from drafts —
worth knowing when you hit other inconsistencies.

---

## 2. Incomplete — not wrong, but you will hit these

### 2.1 Ollama silently truncates at 4096 tokens

Not mentioned anywhere in the spec. Ollama defaults to a **4096-token context
regardless of what the model supports**, and truncates longer prompts with no
error. A prompt built against a 16k budget quietly loses its head.

**Always send `num_ctx`.** `src/agent/models/ollama.py`.

### 2.2 Cold model loads dominate wall clock on CPU

Measured on the dev machine: **~58s cold vs ~5s warm** for the same call.
Without `keep_alive`, weights unload every 5 minutes and every task pays the
reload. Set `keep_alive: 30m`.

### 2.3 Small models cannot follow text output formats

The spec's action protocol (`<action tool="...">`) and the deputy's verdict
format (`VERDICT:` / `BECAUSE:`) are both plain text. A 3B model emits
`<action>list_dir("work")</action>` — right idea, wrong syntax. An 8B reviewer
could not produce the verdict format **at all**, so rule 4 ("false means
reject") denied **100% of actions** and the agent was completely paralysed.

That one would have made a 7-hour benchmark run report 0% on every task and
look like a suite that was too hard.

**Fix:** constrained decoding via Ollama's `format` parameter — which the spec
*does* recommend in M5.4, just only for tool output, not for the action
protocol or the deputy. Both now pass a JSON schema. See `ACTION_SCHEMA` in
`execute/loop.py` and `REVIEW_SCHEMA` in `plan/review.py`.

### 2.4 A `$0.00` budget must not mean "cannot run"

`Budget.exhausted()` as specified returns true when `spent_usd >= usd`, so a
zero-dollar budget is exhausted before the first step — and a fully local task
costs exactly zero. Every local run died instantly.

**Fix:** zero means "free backends only." `src/agent/kernel/types.py`.

### 2.5 The throughput probe breaks invariant 12

`measure_throughput()` as specified makes a real model call. That call
bypasses `record_call`, so it never lands in `calls` — violating the spec's own
invariant 12, "every model call is recorded in `calls`." On a CPU tier it also
triggers a 58s cold load *purely to make a routing decision*, and on a paid
backend it bills you for routing.

**Fix:** routing prefers rates observed from real work and never calls a model.
`probe_throughput()` keeps the measurement as an explicit opt-in.

### 2.6 Prefer Textual over Ink for the TUI

**Spec says** (M13.5): a Node/Ink frontend, because "your kernel is Python and
the mature terminal-UI ecosystem is JavaScript."

That premise is materially weaker now. **Textual** (from the Rich authors) is
established for Python TUIs, and this project already uses Rich. Going Textual
means one language and no JSON-RPC bridge to build and debug — it deletes a
subsystem rather than building one. Ink remains a fine choice if you want
React; this is a recommendation, not a correction.

### 2.7 Testing a function is not testing that the system calls it

Not a spec error, but the single most expensive lesson here. An audit found
**seven** pieces of spec machinery with passing unit tests and **zero call
sites** in the running agent — the ratchet, deviation handling, skill
invocation recording, the anti-gaming signal, and more. The semantic cache sat
dead for a week with a green test.

When you implement a milestone, add a test that the **runtime** exercises it.
See `tests/test_wired_invariants.py`.

---

## 2.8 M7.4 rule 4 (the anti-gaming veto) is INOPERATIVE for this suite

Not ambiguous, not "wired but quiet": it cannot fire.

`record_holdout_gap` has no callers. The only code that partitions checks
into visible and held-out is the M11 generator (`holdout_frac=0.30`), which
has never run. A `verify_program` suite has **one grader per task and no
partition at all**, so there is no visible/held-out gap to compute and
`_holdout_gap` always returns `None`.

The gate previously skipped the veto silently, which made a promotion that
never ran the check look identical to one that passed it. Every promotion now
carries `gaming_check`, either `"ran"` or `"NOT MEASURED — no visible/held-out
partition exists for this suite, so M7.4 rule 4 was skipped"`.

This mattered more than it looks. Until 2026-08-27 the grader lived at
`work/<key>/verify.py` — **inside `artifact_scope`** — with `EXPECTED` in
plaintext. A generated skill whose procedure was "read verify.py and echo the
expected value" would have scored 100% on every task and presented as the
largest lift ever measured, with the one guard designed to catch it unable to
run. The grader now lives at `grading/<key>/`, outside every path the agent
can reach.

## 2.9 Three corrections the author retracted after seeing the data

Recorded here because they are errors in the original documents, not drift:

1. **`cache_hit_ratio` as a monitored invariant.** It is a cloud-billing
   metric with no local equivalent. `cache_read_tokens` is populated only by
   the mock backend; across 566 real local calls it is 0. The acceptance test
   passed only because the mock fabricates a hit. Report `n/a` under
   `--profile local` and drive the assertion from `PrefixGuard` fingerprint
   stability instead.

2. **The difficulty tiering** ("Easy ~80%, Medium ~50%, Hard ~20%, mean
   ~50%"). Written for an unspecified model. The sensitivity band is a
   property of *suite × executor*, not of the suite. On `granite4.1:3b` Q4,
   10% may be the honest number.

3. **The scorer, `max(0.0, 1 - rel/TOL)`.** A step function outside
   tolerance, which is where 14 of 15 wrong-number tasks live. Every wrong
   answer scores exactly 0.0, so the M7 ratchet has no gradient to climb and
   `plateau` fires after three identical zeros. See §2.10.

## 2.10 The ratchet has no gradient, and faking one would be worse

Given the choice between making the scorer monotonic in `log(rel)` and
declaring M7 unexercised, the honest answer is **both, in that order**: the
scorer is now `1/(1 + log10(rel/TOL))` outside the band, which is monotonic
and gives the ratchet a real ordering — a 3% miss now scores above a 300%
miss instead of both scoring 0.

But that gradient does **not** make M7 exercised by this suite, and the
report must not imply it does. The agent is told only a scalar; it is not
told which key was wrong, in which direction, or by how much. A monotonic
score lets the *ratchet* keep the better of two candidates. It does not give
the *model* an external observation it can act on, which is what refinement
needs. Measured on pass 1: of 30 tasks, exactly one ever moved its score.

## 3. Still valid — NOT corrections

Follow the spec on all of these. They are not errors and nothing here
supersedes them:

- **The research thesis and every piece of evidence behind it.** The +16pp
  human-written vs −1pp self-generated finding, the 41–87% multi-agent failure
  rates, the 30-point long-context degradation, the 36% flawed-skill audit,
  the 28.4% injection success rate. These are the reasons the invariants exist.
- **The milestone ordering**, especially "build M9's gate before M11's
  generator" and "do not enable unattended operation before M8." Both are
  load-bearing and both were followed.
- **The full invariant checklist** (Part 15). All 17 hold; six more were added.
  See `INVARIANTS.md`.
- **The two-touch interaction model** and everything in M13.
- **The cloud/API tier.** Not yet exercised — there is no API key — but the
  design is sound and the adapter is written. This is *unfinished*, not wrong.
  See `docs/FINDINGS.md` 1.25, which inventories what has never run for real.
- **Muse Glimmer 30B as a future desktop tier** if you get a 24–32 GB GPU. The
  30B is real; only the 3B was a misreading.
- **The benchmark honesty guidance** (Part 13): cost-vs-quality Pareto rather
  than a single score, report three ways, contamination caveats in the same
  paragraph as the number. Followed in `docs/` and `bench/run.py`.
- **The claim to actually make** (§13.4). Unchanged and still the goal.

---

## How to use these three documents together

For a coding agent picking this up, the reading order that works:

1. **`docs/HANDOVER.md`** — what exists, how to run it, what is incomplete, what to
   do next. Start here.
2. **`spec/CORRECTIONS.md`** — this file. Read before opening the spec.
3. **`spec/BUILD-SPEC.md`** and **`spec/EXPLAINER.md`** — the reasoning behind
   every design decision. Read when you need to know *why* an invariant exists
   before changing code near it.
4. **`INVARIANTS.md`** — the pre-commit checklist.

The spec is a design document, not a build script. Where it and the running
code disagree and this file does not explain the difference, **the code is
probably right and the discrepancy is worth a test.**
