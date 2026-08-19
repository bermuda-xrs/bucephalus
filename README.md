# alpha test 1

A hybrid local/cloud agent with verified skill learning, built from
`BUILD-SPEC.md`. You state an objective, review one plan, and do not touch it
again until it reports back.

The claim it is built to test is narrower than "ten times cheaper":

> Good procedures make a cheap model competent enough to do most of the work
> — and that can be measured rather than asserted.

Everything here exists to keep that measurement honest.

## Running it

**The package is deliberately not installed, and the venv lives outside this
folder.** Two environment facts forced both choices:

1. `pip install -e .` fails at this path. Setuptools writes its `.pth` file
   with the system ANSI codepage (cp949 here), which cannot encode the Greek
   `ά` in `Βουκεφάλας`. The launcher sets `PYTHONPATH` instead.
2. OneDrive deleted the in-folder `.venv` twice. A venv is thousands of tiny
   files, which is exactly what an unstable sync loses; the 2 GB model blobs
   next door survived fine. The venv now lives at
   `%USERPROFILE%\.alpha-test-1-venv` and the launcher finds it there.
   Move it back to `.venv` once sync is healthy — the launcher checks that
   location first.

```bat
alpha.cmd status
```

```powershell
.\alpha.ps1 status
```

Tests:

```bat
%USERPROFILE%\.alpha-test-1-venv\Scripts\python.exe -m pytest tests -q
```

## Running it with no API key at all

There is a second profile that runs the entire system on the local Granite
models — no key, no network, no spend. Start the dedicated Ollama instance in
one window and leave it open:

```bat
ollama-serve.cmd
```

Then in another window:

```bat
alpha.cmd --config config.local.yaml new "Read work/note.txt and report what it says." --scope work --seconds 1800
alpha.cmd --config config.local.yaml run t_xxxxxxxx
```

Verified working end to end: `done` in 2 steps, ~27s, 1,868 free tokens,
$0.0000, hash chain verifying.

Be honest about what this is. A 3B model at ~2 tok/s is not a good agent — one
turn costs 15-40 seconds. The profile exists so the whole pipeline is
genuinely runnable and inspectable without a paid key, which matters a lot
more for understanding the system than raw capability does. Keep tasks small.

**Three things make it usably fast**, all measured on this laptop:

- **`num_ctx` is sent explicitly.** Ollama otherwise defaults to a 4096-token
  window whatever the model supports, and silently truncates the rest — so a
  prompt built against a 16384 budget lost its head with no error. This was a
  correctness bug wearing a performance costume.
- **`keep_alive: 30m`.** A cold load is ~58s against ~5s warm. Without it the
  weights unloaded every 5 minutes and every task paid the reload.
- **The semantic cache is wired** into observation condensing. Three identical
  requests cost one model call and two hits. Zero tokens beats cheap tokens,
  and on a CPU tier zero seconds beats 30.

The reviewer is `granite4.1:8b` while the executor is `granite4.1:3b`, because
invariant 14 requires the deputy to be a different backend from the executor —
and that rule earns its keep here rather than being satisfied on paper.

Two things make this work that are worth knowing about:

- **Constrained decoding.** Left to itself the 3B emits
  `<action>list_dir("work")</action>` — right idea, wrong syntax. M5.4 says to
  fix that with a schema rather than a retry loop, so ollama backends get
  `ACTION_SCHEMA` through Ollama's `format` parameter and answer in JSON. The
  cloud tier keeps the tag protocol, where forcing a schema would fight
  extended thinking. `parse_action` accepts both shapes.
- **A budget of `$0.00` means "free backends only"**, not "cannot run".
  Before that fix a fully local task was declared exhausted before its first
  step, because `spent_usd >= usd` is true at zero.

## The two-touch loop

```
alpha.cmd new "Refactor work/parser.py to use pathlib" --scope work
alpha.cmd plan   t_abc123      # drafts a plan. Cannot write: enforced in the gate.
alpha.cmd approve t_abc123     # HUMAN TOUCH #1
alpha.cmd run    t_abc123      # runs unattended. HUMAN TOUCH #2 is the result.
```

Between those two touches the agent does not interrupt you. Boundary crossings
go to the deputy (a different model from the executor), which decides. Only
trusted-tier actions park, and the plan tells you in advance which steps those
will be — the `will PARK for you at:` line is the field worth reading before
you walk away.

Other commands:

```
alpha.cmd status              list tasks, cost, and anything waiting on you
alpha.cmd approvals           decide what parked
alpha.cmd alerts              integrity breaks + monitor findings (NOT approvals)
alpha.cmd bench-local         measure local tier throughput before trusting it
alpha.cmd report <task>       per-run JSON + CSV of every model call
alpha.cmd pareto              cost vs pass rate across arms
alpha.cmd steer <task> "..."  correct a task — forks it, never mutates
alpha.cmd monitor <task>      run the out-of-band inspector over a trace
alpha.cmd skills                          list skills + promotion rate
alpha.cmd skills --add S.md --when "..."  store a hand-written skill (candidate)
alpha.cmd skills --gate <id> [--yes]      evaluate, then promote with a human
alpha.cmd serve               control plane on :8901 for phone/web clients
```

The control plane also runs under any ASGI server via a module-level app:

```bat
set ALPHA_CONFIG=config.local.yaml
uvicorn agent.control.asgi:app --host 127.0.0.1 --port 8901
```

Verified working: `POST /tasks` creates, `GET /tasks/{id}` returns state and
budget, `/metrics/{id}` returns telemetry, and `/alerts` stays separate from
`/approvals`. **One worker only** — the hash chain assumes a single writer,
and the lock protecting it is process-wide.

## Measuring a skill, start to finish

This is the M9 loop the whole project exists to test:

```bat
alpha.cmd skills --add my-skill.md --name tidy --when "cleaning a messy csv"
python bench\run.py paired <skill_id> --suite bench\tasks
alpha.cmd skills --gate <skill_id>          REM verdict + lift + p-value
alpha.cmd skills --gate <skill_id> --yes    REM promote, requires a human
```

Nothing added by `--add` is ever live: it lands as a `candidate`, and only the
gate promotes, only after 30 paired trials clear all four conditions, and only
with a human saying yes. A 4-task starter suite is in `bench/tasks/` — it will
correctly report `insufficient`, because 4 is not 30. Write your own 30; that
suite is the denominator of every number this system reports.

`demo.py` runs the whole flow with a scripted model — no API key, no network.

## Models

Both local tiers are **Granite 4.1**, one family, so there is one chat
template, one tool-call syntax, and one code path across machines. That is
what §0.1 asks for.

| tier | model | size | role |
|---|---|---|---|
| `local` | `granite4.1:8b` | 5.3 GB | desktop tier |
| `laptop` | `granite4.1:3b` | 2.1 GB | constrained tier, thinking off |
| `cloud` | `claude-opus-5` | — | planning, reasoning, execution |
| `review` | `claude-sonnet-5` | — | the deputy, a different model by design |

**The spec's `muse-glimmer:3b` does not exist.** All 15 Ollama tags for Muse
Glimmer are 30B. The "3B assistant model" in the release notes is
`Muse-Glimmer-30B-assistant`, a DFlash block-diffusion *drafter* for
speculative decoding — not a chat model, and it needs the 30B loaded beside
it. The smallest 30B quant is 17 GB, which does not fit this machine
(15.5 GB RAM, 2 GB shared Intel iGPU — §0.1's "no GPU" row). Restore
`muse-glimmer:30b` as `local` if you move to a 24–32 GB GPU box.

Models live in `../ollama-models` and are served by a dedicated Ollama
instance on port **11435**, so your existing models on 11434 are untouched:

```bat
set OLLAMA_MODELS=..\ollama-models && set OLLAMA_HOST=127.0.0.1:11435 && ollama serve
```

Run `alpha.cmd bench-local` before trusting either tier. On CPU-only hardware
output tokens are wall-clock seconds, and §0.1c says to settle model choice
from your own measurements rather than someone else's leaderboard.

## Where the local tier actually does work

The router, the allowlist and escalation-by-disagreement are only worth
anything if something in the loop asks for a local-eligible purpose. One does:
**tool outputs above `compress_observations_over_tokens` are condensed by the
local tier** before entering the ledger (`compress_tool_output`, on both
allowlists). That is the high-volume, low-risk work.

When the output is `untrusted`, the same call runs under the QUARANTINE prefix
with `tools=None` — M8.1 rule 4 — so only a summary plus an artifact reference
crosses back, and an injection inside the content has nothing to actuate.

If no local tier is configured, compression is skipped entirely: paying cloud
rates to shrink a prompt you are about to send to the cloud is a net loss.

## What it does and does not do

**Will:**
- take an objective, plan it, and run unattended after one approval
- split work between the free local tier and the paid one, and show the split
- survive a crash and resume without redoing side effects
- refuse actions that cross permission boundaries
- record every number needed to check its own claims

**Won't, yet:**
- operate a graphical desktop or browser
- claim to be secure against a determined attacker
- have polished visuals — there is no Ink/Textual TUI, only the CLI
- talk to a paid API — the `anthropic` adapter is written and unit-tested but
  has never made a live call

## Grounding, fetching, and why not a server-side tool

`web_fetch` uses **our own fetcher** (`src/agent/net/fetch.py`), not
Anthropic's server-side `web_fetch`. That is deliberate. Server-tool results
arrive inside the model's reply, so those bytes never pass through
`write_artifact(taint=UNTRUSTED)` or `TaintTracker.observe()` — the capability
gate would have no idea they came from the internet, and M8's whole defense
keys off provenance the tool path establishes. Using the server tool would
quietly delete the one guarantee this build demonstrates.

The fetcher is small on purpose: http/https only, private and loopback
addresses refused (a fetched page names the next URL — without that check
"summarize this page" is one hop from the router's admin console), a hard byte
cap enforced while streaming, text content types only, HTML reduced to text.

Skill grounding (M11) reads from a `grounding/` folder in the project, or from
any URL in the pattern description, fetched the same way. There is deliberately
no search engine: the spec's finding is that self-generated skills drift
negative when drafted from model priors, and substituting "whatever a search
API returned" swaps one ungrounded source for another.

## Verifiers that check the answer

`json_schema` checks *shape* — `{"total": 0}` passes for an invoice totalling
4825. Two verifiers check *correctness*, and the bench suite uses them:

| verifier | declares | checks |
|---|---|---|
| `expected_value` | `x-expected` in output_schema | the answer equals the known-correct value (numeric tolerance, whitespace-insensitive strings unless `x-exact`) |
| `contains_all` | `x-contains` | every required substring survived |

The expected answer lives on the task file and never enters the agent's
context. Verified live: the 3B returned `{"total": 4825}` and
`expected_value` passed it — a pass that could have failed.

**Might not work at all:** the skill-learning part. That is the research bet.
Published evidence is that self-generated skills average slightly *negative*.
The gate exists so that failing is visible instead of silent.

## Layout

```
src/agent/
  kernel/      M1  event log, hash chain, anchor, receipts, task graph
  instrument/  M2  cost math, per-call telemetry, reports
  ledger/      M3  artifacts by reference, cache-stable prefix, compaction
  models/      M4  anthropic + ollama + mock backends
  execute/     M4/M5 the loop, tools, sandbox, code-as-action + tool bridge
  router/      M6  allowlist routing, escalation by self-consistency
  assure/      M7/M8.3 verifiers, ratchet, anti-gaming, out-of-band monitor
  security/    M8  taint propagation, capability gate
  skills/      M9/M11 store, retrieval, THE GATE, generator
  delegate/    M10 five-gate contract test
  plan/        M13 plan artifact, planner, auto-review, deviation
  control/     M12 FastAPI control plane + client event schema
  cli.py       M13 terminal client
bench/         Part 13 harness + a starter task suite (write your own)
tests/         the spec's acceptance tests, per milestone, + source invariants
```

## Configuration worth knowing about

- **`backends.cloud.model`** is `claude-opus-5`. The spec named
  `claude-sonnet-4-6` and said to verify current model names. Two API facts the
  adapter handles that the spec predates: `temperature` is rejected outright by
  current models (a 400, not a warning), and thinking is `{"type": "adaptive"}`
  with depth set by `output_config.effort` — `budget_tokens` is gone. Switch
  models by editing the config; also update the four cost numbers, or your
  telemetry lies.
- **`usable_context_window`** sits alongside `context_window`. The advertised
  window is real; the budget is not the same thing. M3.4 measures the fill
  ratio against the usable figure, because long-context degradation arrives in
  cliffs well below the maximum.
- **`autonomy.auto_review_backend: review`** is a separate backend on a
  different model from `cloud`. The spec's own config set this to `cloud`
  while the executor is also `cloud`, which trips the anti-collusion assertion
  on the first review the system performs.
- **`local_allowlist`** starts at five purposes. Expand it only from
  `skill_trials` data. `plan` and `review` are deliberately absent.

`policy.md` is the deputy's rule book, in plain text, edited by you. Its
default is deny: a category you have not written a rule for is refused, and a
missing policy file refuses everything.

## Token counting

The spec listed `tiktoken`. It is not used: that is OpenAI's tokenizer and it
undercounts Claude tokens by 15–20% on prose and more on code, so every cost
number derived from it would be quietly wrong. `src/agent/tokens.py` uses a
fast local estimate for in-loop budgeting and the provider's own
`count_tokens` endpoint for calibration and for anything published. Run
`calibrate()` on your own corpus before quoting a token figure.

## The sandbox, honestly

A subprocess is a safety measure against mistakes, not a security boundary
against an adversary. An audit hook inside the child confines writes to the
workdir and the paths linked into it, so the spec's "code attempting to write
`anchor_path` fails" is genuinely true — but an audit hook lives in the same
process it polices. If you ever run genuinely untrusted code, use Docker with
`--network=none`, a read-only root, a non-root user, and dropped capabilities.

`call_tool(...)` inside sandboxed code round-trips to the parent over
stdout/stdin and is gated exactly like a direct action, with side effects
receipted. An action that would need human approval is refused there rather
than parked — a sandboxed process cannot be suspended mid-run.

On Windows there are no `rlimit` caps, so memory and CPU limits are not
enforced — only the wall-clock timeout. `run_sandboxed` reports that in
`notes` rather than pretending otherwise.

## Reading the numbers it produces

Every report carries its provenance and the caveats in the same object as the
figures, not in a footnote. Report three ways — bare model, model + scaffold,
full system — with date and versions. A believable number with its flaws
disclosed beats an impressive one nobody can check.

See `INVARIANTS.md` for the pre-commit checklist, the four places where the
build spec's code snippets contradicted its own acceptance tests, and the two
spec instructions that could not be followed as written.
