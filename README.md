# Bucephalus

**An agent runtime with verified skill learning.** Not a benchmark harness:
the harness exists to keep the runtime honest, and the runtime is the
deliverable. You state an objective, review one plan, and do not touch it
again until it reports back.

The claim it is built to test is narrower than "ten times cheaper":

> Good procedures make a cheap model competent enough to do most of the work
> — and that can be measured rather than asserted.

Everything here exists to keep that measurement honest.

## Install

```bash
pip install bucephalus
```

State — the database, hash-chain anchor, run records, workspaces, graders and
skills — goes to your platform's user data directory. Running from a source
checkout keeps state beside the source instead, which is what `bench/`
expects. Nothing is ever written next to `site-packages` or into whatever
directory you happen to be standing in.

Apache 2.0.

## Models

**Local**: any Ollama model. The measurements here used `granite4.1:3b`
(3.4B, Q4_K_M) and `granite4.1:8b` on an Intel Core Ultra 5 225U, 15.5 GB
RAM, CPU inference. No API key, no network.

**Cloud**: bring your own API key —

```bash
export ANTHROPIC_API_KEY=sk-...
```

**The cloud adapter is UNVERIFIED.** It is written and unit-tested and has
never made a live call. Do not describe it as working until it has. There is
no subscription login and none is planned; it is an API key or nothing.

## Running it

**The package is deliberately not installed, and the venv lives outside this
folder.** Two environment facts forced both choices:

1. `pip install -e .` fails at this path. Setuptools writes its `.pth` file
   with the system ANSI codepage (cp949 here), which cannot encode the Greek
   `ά` in `Βουκεφάλας`. The launcher sets `PYTHONPATH` instead.
2. OneDrive deleted the in-folder `.venv` twice. A venv is thousands of tiny
   files, which is exactly what an unstable sync loses; the 2 GB model blobs
   next door survived fine. The venv now lives at
   `%USERPROFILE%\.bucephalus-venv` and the launcher finds it there.
   Move it back to `.venv` once sync is healthy — the launcher checks that
   location first.

```powershell
.\bucephalus.ps1 status
```

`bucephalus.cmd` does the same from `cmd.exe`, but only at a path without
non-ASCII characters: `cmd.exe` reads a batch file in the OEM codepage, so at
this folder's path it fails silently (HANDOVER B1). The examples below use
`.\bucephalus.ps1`, which works anywhere.

Tests:

```bat
%USERPROFILE%\.bucephalus-venv\Scripts\python.exe -m pytest tests -q
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
.\bucephalus.ps1 --config config.local.yaml new "Read work/note.txt and report what it says." --scope work --seconds 1800
.\bucephalus.ps1 --config config.local.yaml run t_xxxxxxxx
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
.\bucephalus.ps1 new "Refactor work/parser.py to use pathlib" --scope work
.\bucephalus.ps1 plan   t_abc123      # drafts a plan. Cannot write: enforced in the gate.
.\bucephalus.ps1 approve t_abc123     # HUMAN TOUCH #1
.\bucephalus.ps1 run    t_abc123      # runs unattended. HUMAN TOUCH #2 is the result.
```

Between those two touches the agent does not interrupt you. Boundary crossings
go to the deputy (a different model from the executor), which decides. Only
trusted-tier actions park, and the plan tells you in advance which steps those
will be — the `will PARK for you at:` line is the field worth reading before
you walk away.

Other commands:

```
.\bucephalus.ps1 status              list tasks, cost, and anything waiting on you
.\bucephalus.ps1 approvals           decide what parked
.\bucephalus.ps1 alerts              integrity breaks + monitor findings (NOT approvals)
.\bucephalus.ps1 bench-local         measure local tier throughput before trusting it
.\bucephalus.ps1 report <task>       per-run JSON + CSV of every model call
.\bucephalus.ps1 pareto              cost vs pass rate across arms
.\bucephalus.ps1 steer <task> "..."  correct a task — forks it, never mutates
.\bucephalus.ps1 monitor <task>      run the out-of-band inspector over a trace
.\bucephalus.ps1 skills                          list skills + promotion rate
.\bucephalus.ps1 skills --add S.md --when "..."  store a hand-written skill (candidate)
.\bucephalus.ps1 skills --gate <id> [--yes]      evaluate, then promote with a human
.\bucephalus.ps1 serve               control plane on :8901 for phone/web clients
```

`serve` prints a link to the console. See "The console" below.

The control plane also runs under any ASGI server via a module-level app:

```bat
set BUCEPHALUS_CONFIG=config.local.yaml
uvicorn agent.control.asgi:app --host 127.0.0.1 --port 8901
```

Verified working: `POST /tasks` creates, `GET /tasks/{id}` returns state and
budget, `/metrics/{id}` returns telemetry, and `/alerts` stays separate from
`/approvals`. **One worker only** — the hash chain assumes a single writer,
and the lock protecting it is process-wide.

## The console

`serve` prints a link to a web console on 127.0.0.1. The bearer token lives in your config folder, outside the
project, and every route needs it except the one webhook route below.

- **Work:** conversations whose follow-ups see the earlier exchanges, a plan card you sign before anything runs, Pause
  and Resume, approvals that park inline, Markdown answers, export, pinning, bulk archive of idle conversations, and
  a file browser per task (make a folder, remove a file you added; your files are marked untrusted).
- **Watch:** Graph (every conversation and task as one graph: a tree of conversations zoomed out, each task's
  steps zoomed in), Analytics (usage and cost by day, model and origin; measurement runs are left out), Logs, Integrity (the
  hash chain, schedules and memory), System (host, disk, a backup of the database, anchor and memory), and Docs
  (this project's own documents, read only).
- **Control:** Schedules (add, pause, resume, run now, edit; each is sealed in the anchor, and a tampered one will not
  fire), Models (what new tasks run on, and the reviewer, for a backend marked `model_server: ollama`; it starts the
  project's server on 11435 if needed and never touches the Ollama on 11434), MCP (the servers in your config, and a
  test that lists a server's tools), Skills, Suites and Policy.
- **Set up:**
  - *Keys* sets or clears the Telegram token and each backend's API key. It is write only: nothing is ever shown back.
    The file is private to your config folder, refused inside a synced folder, left out of backups, and protected from
    the agent. An environment variable of the same name wins.
  - *Config* edits a closed set (limits, budgets, `long_files`, each backend's model). You review a diff, type
    `apply config`, and tick any loosening (a limit raised tenfold; reviewer and executor the same model). The file is
    backed up first, comments are not kept, and the change takes effect at the next start. Anything that widens what the
    agent may do stays in the file for you to edit by hand.
  - *Channels* allows or revokes a Telegram sender by typing their id twice. Their tasks stay channel tasks: a smaller
    budget, no approving over the chat.
  - *Webhooks* lets a script on this computer hand the agent a task: `POST /hooks/{name}` with a secret and a delivery
    id. It creates a pending task whose plan you still sign; the payload is saved as an untrusted file, never put in
    the goal. Loopback only, whatever `BUCEPHALUS_ALLOW_REMOTE` says.

What it does not do, and why (DEFERRED D81, D83): install skills, MCP servers or plugins from the network, update or
restore from the page, send your data anywhere, delete a session, or take a webhook from another machine. The rule
behind the list is that the console may read, supervise and make bounded, reviewed changes. It never fetches or runs
code from the network, never sends your data to a third party, and never gives the agent a power you did not sign for.

## Measuring a skill, start to finish

This is the M9 loop the whole project exists to test. The order is not
optional: **the noise floor comes first**, because a lift is only meaningful
relative to how much the same tasks move on their own.

```powershell
# 1. how noisy is the suite? Baseline arm, no skill loaded.
#    Pass 1 first, then read it before spending the rest.
python bench\noise.py --config config.local.yaml --profile local --suite aero_calc --runs 1 --out runs\noise-floor.json
python bench\noise.py --config config.local.yaml --profile local --suite aero_calc --runs 3 --resume --out runs\noise-floor.json

# 2. the calibration skill - a POSITIVE CONTROL on the instrument (D35).
#    Drafted from the drafting split only, declared so, scored on eval only.
#    In agent.local.db it is already stored and declared: a4aa91a3e464.
.\bucephalus.ps1 --config config.local.yaml skills --add examples\skills\si_first_orbital_arithmetic.md ^
    --name si_first_orbital_arithmetic --when "aerospace calculation from params.json and constants.json" ^
    --drafted-from E01,E02,E03,E04,E05,M11,M12,M13,M14,M15,M16,H23,H24,H25,H26
python bench\run.py --config config.local.yaml --profile local paired <skill_id> ^
    --suite aero_calc --split eval --out runs\calibration.json

# 3. the verdict, and promotion, which requires a human
.\bucephalus.ps1 --config config.local.yaml skills --gate <skill_id>
.\bucephalus.ps1 --config config.local.yaml skills --gate <skill_id> --yes

# 4. the run sheet, with the caveats attached to the numbers
python bench\report.py --out docs\run-sheet.md
```

Nothing added by `--add` is ever live: it lands as a `candidate`, and only the
gate promotes, only after the paired trials clear all four conditions, and
only with a human saying yes.

**Step 2 runs on the eval split, by the rule every skill follows.** The
control was drafted from the 15 drafting tasks, and the contamination check
refuses each of them; `--split eval` keeps the run to the 15 it may be scored
on. An undeclared drafting set certifies no trial at all (D29's sweep).
`aero_units_discipline`, the earlier control, declares the whole suite
truthfully - its author saw every task - so it is refused on all 30. That is
the check working. It stays as the record of the 10-pair run of 2026-08-27 and
is not runnable. A skill written blind to the suite: `--add ... --drafted-from none`.

**Both long runs are resumable.** They append each completed run to a
`.partial.jsonl` ledger (noise floor) or to `skill_trials` (calibration), so a
kill costs at most one task. `--resume` picks up where it stopped and refuses
to blend results from a different suite, split, or profile.

**Which conditions actually bind.** `min_discordant_pairs` is the real guard
— McNemar reads only discordant pairs, and roughly 75-80% of disagreements
must favour the skill at any n. `min_paired_trials` is a floor on the pool,
not a statistical guard. `min_lift_pp` is **inert** below n=160 at these
settings and is not protection. See the annotated `gate:` block in
`config.local.yaml`.

**Contamination.** The eval split never enters a drafting prompt, and every
skill - the hand-written control included - is scored with `--split eval` on
tasks it was not drafted from. The report's caveat is read from the skill's
own provenance: who drafted it, whether any scored task is in its declared
drafting set, and what it declares about exposure. It is printed next to the
number, not in a footnote. At n=15 the gate needs 9 of 10 discordant pairs
to favour the skill, so the control can fire only on a large effect, and a
null says only that the effect was not large.

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

Run `.\bucephalus.ps1 bench-local` before trusting either tier. On CPU-only hardware
output tokens are wall-clock seconds, and §0.1c says to settle model choice
from your own measurements rather than someone else's leaderboard.

## Where the local tier actually does work

The router and the allowlist are only worth anything if something in the loop asks for a local-eligible purpose. One does:
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

**Skill generation (M11) is retired, and its research question with it.**
Do self-generated skills help? This project never answered that: the
generator never ran. It was retired on 2026-09-15, along with the M7.4 rules
that policed it. The published baseline is -1.3 pp, the suite that would
have evaluated it is archived, and D19 is unclosed. FINDINGS 1.27 says what
would reopen it, and where the code is in git. Skills here are hand-written
and pass the same gate. Every promotion states why the gaming veto does not
apply: every check is held out, and the grader is unreachable.

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

Skill grounding was M11's, and it left with the generator (FINDINGS 1.27).
There was deliberately no search engine behind it: the spec's finding is that
self-generated skills drift negative when drafted from model priors, and
substituting "whatever a search API returned" swaps one ungrounded source for
another.

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
  models/      M4  anthropic + ollama + openai-compatible + mock backends; local server control
  execute/     M4/M5 the loop, tools, sandbox, code-as-action + tool bridge
  router/      M6  allowlist routing
  assure/      M7/M8.3 verifiers, ratchet, out-of-band monitor
  security/    M8  taint propagation, capability gate
  skills/      M9 store, retrieval, THE GATE (M11's generator is retired)
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

- **`backends.<slot>.tool_profile`**: `full` (default, `edit_file`), `patch` (`apply_patch` instead) or
  `small` (six tools, no code-as-action, `read_first` and `read_all_pages` on). Only the executor slot's profile
  applies. `read_first`, `read_all_pages` and `observation_tokens` override the profile's defaults; `read_all_pages`
  turns an answer given while an opened file has unread pages into a reminder naming them, three times at most.
- **`long_files`**: how a file longer than the model can take at once reaches it. `pages` (default): `read_file`
  returns one page sized to the context, ending with the call for the next. `elide`: head and tail, the middle on
  request. `whole`: the fill ratio rises to 0.75 so it fits in full. The console's Settings can change it for new tasks.
- **`hooks`**: commands you declare, run outside the sandbox with an allowlisted environment (`PATH`, temp and home
  directories, locale) plus the hook's own `env`; a Python hook runs with `-I`. `pre_tool` runs
  after the gate and can only refuse (a hook that fails, hangs or cannot start refuses); `post_tool` output is
  appended as untrusted; `stop` can hold an answer back three times. Each gets the call as JSON on stdin.
  The config file, every file named in a hook's or MCP server's `argv`, its directory (unless that is the project
  root) and a `-m` package are protected state. What a hook collects (a `conftest.py` under a `pytest` hook) is
  not; keep it out of every task's scope.

  ```yaml
  hooks:
    pre_tool:
      - argv: ["python", "C:/tools/no_secrets.py"]
        tools: [write_file]
        timeout_s: 10
  ```
- **`mcp_servers`**: stdio MCP servers whose tools join the registry as `mcp__<server>__<tool>`. A tool writes
  unless listed in `read_only`; output is untrusted; the server's own descriptions never reach the prompt; path-like
  arguments are scope-checked. A writing tool should declare its path arguments in `path_args` (`{tool: [arg, ...]}`);
  one that does not parks every call for a human and has any string without whitespace (`https://` URLs excepted)
  scope-checked. `strict_paths: true` checks every string. A server gets the hooks' environment allowlist plus its own
  `env`. Unattended origins cannot call them.

  ```yaml
  mcp_servers:
    time:
      argv: ["C:/venvs/mcp/Scripts/python.exe", "-m", "mcp_server_time"]
      read_only: [get_current_time, convert_time]
  ```
- **`AGENTS.md`** beside the config enters the prompt as project notes, frozen per task, unless the task could write
  it (inside the scope, a link, or a hard link); tasks cannot write it.
- **New commands**: `bucephalus exec "goal" --scope work [--output-format stream-json] [--approve-plan]` runs one
  task headless (exit 0 done, 2 not done, 3 plan needed, 4 parked); `bucephalus say <task> text` speaks to a running
  task; `bucephalus diff <task>` shows what each step changed; `bucephalus rewind <task> [--step N] [--what
  code|conversation|both] [--undo]` puts it back. The console has the same as Changes, Rewind and `/say`.

`policy.md` is the deputy's rule book, in plain text, edited by you. Its
default is deny: a category you have not written a rule for is refused, and a
missing policy file refuses everything.

## Token counting

The spec listed `tiktoken`. It is not used: that is OpenAI's tokenizer and it
undercounts Claude tokens by 15–20% on prose and more on code, so every cost
number derived from it would be quietly wrong. `src/agent/tokens.py` is a
fast local estimate, used only for in-loop budgeting. Recorded usage - every
token figure in `calls` and in a report - is the model server's own count
(Ollama's `prompt_eval_count` / `eval_count`, the OpenAI protocol's
`prompt_tokens` / `completion_tokens`, Anthropic's `usage`). Every call row
keeps the raw estimate beside the server's count, and once a backend has 30
such calls the context ceiling budgets against a fit of one to the other
(HARNESS_VERSION 7). On granite4.1:3b the raw estimate undercounts by about a
quarter.

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

**Resource limits are enforced by the OS on both platforms, except file size
on Windows.**
- **POSIX:** the child gets `RLIMIT_AS` (memory), `RLIMIT_CPU`, `RLIMIT_NPROC`
  and `RLIMIT_FSIZE`. That path has never run.
- **Windows** (since 2026-09-15, D37): the child runs inside a Job Object with
  the same numbers - 512 MB per process, CPU time equal to the timeout, 32
  processes - and whatever it starts dies when the job closes.

On Windows, allocating past the limit fails, spinning past it is killed, and a
33rd process is refused. The venv launcher is started suspended, so the
interpreter it starts is born inside the job. If the job cannot be applied,
the code does not run at all.

**File size is not limited on Windows**, and now stays that way: a container
was the only per-process route to it and **Phase 6 is withdrawn** (2026-09-16,
FINDINGS 1.29). `run_sandboxed` says so in every run's `notes`. The sandbox is
advisory path confinement, a Job Object, and a wall-clock timeout - stated,
and accepted (D32).

**It is a door, not a wall, and only Phase 6 makes it a wall.** For agent
code the hook refuses writes outside the task's scope; reads outside that
scope and the interpreter's own files; the project's own state by every file
route tested (SPEC-DELTA C50) and every database route (C55); starting a
process; and loading a native extension. That stops a model that REACHES for
something it should not. It does not stop code written to get past it: the
hook lives in the process it polices, so `ctypes`, a native library loaded
some other way, a `sqlite3.Connection` built without `connect()`, or
`set_authorizer(None)` goes straight through (DEFERRED D32). The wall is a
container per run - no network, a read-only root, a non-root user - which is
Phase 6, parked because Docker is not installed here; installing it is your
decision. Until then, nothing this project produced should be read as having
run untrusted code safely.

## Two paths no live request has ever tested

**The Telegram transport and the Anthropic adapter are both unverified against
the live service.** Neither has made one real request. Each has a test that
makes one the moment its credential exists, and skips until then:

| path | test | runs when | what it does |
|---|---|---|---|
| Telegram HTTP transport | `tests/test_live_credentials.py::test_telegram_transport_live` | `BUCEPHALUS_TELEGRAM_TOKEN` is set | `getMe`, a `getUpdates` at offset 0 and timeout 0 (read-only; confirms nothing), and one unknown method that must be refused |
| Anthropic adapter, streaming | `tests/test_live_credentials.py::test_anthropic_streaming_live` - once per Anthropic backend in `config.yaml` | `ANTHROPIC_API_KEY` is set | one tiny streamed call with that backend's exact request shape; text must reach the surface in chunks, and usage, stop reason and model must come back |

**A key in the environment makes the full suite spend** - two small paid calls,
a few hundred tokens each. A skipped test proves nothing about its body, so
the same bodies run on every suite against local fake servers speaking each
protocol (`test_the_telegram_body_against_a_fake_api`,
`test_the_anthropic_body_against_a_fake_api`); that is how these were checked
to catch a broken transport rather than pass it.

### When you have a credential, this is the whole procedure

One command each. Nothing else needs doing: the tests unskip on the
environment variable alone.

```powershell
# Telegram. A bot token from @BotFather; the test only READS.
# (This test reads the environment variable only, not the console's Keys page.)
$env:BUCEPHALUS_TELEGRAM_TOKEN = "123456:ABC..."
..\..\..\.bucephalus-venv\Scripts\python.exe -m pytest tests/test_live_credentials.py -k telegram -v

# Anthropic. One small streamed call per Anthropic backend in config.yaml.
$env:ANTHROPIC_API_KEY = "sk-ant-..."
..\..\..\.bucephalus-venv\Scripts\python.exe -m pytest tests/test_live_credentials.py -k anthropic -v
```

**A pass looks like `1 passed` (Telegram) or `2 passed` (Anthropic, one per
backend) and nothing skipped.** A skip means the variable is not visible to
that shell, which is the only way to get a false pass here.

**What a failure would mean, per path:**
- **Telegram:** `getMe` rejected the token, `getUpdates` returned a shape the
  transport does not parse, or an unknown method was *not* refused - the last
  is the one that matters, because it is the guard.
- **Anthropic:** the request shape was rejected, or text, usage, stop reason
  or model did not come back as the adapter assumes. Either way the adapter's
  `UNVERIFIED` banner comes off only when this passes.

**Unset the variable afterwards** unless you want every future suite run to
spend: the two live tests cost a few hundred tokens each time.

## Reading the numbers it produces

Every report carries its provenance and the caveats in the same object as the
figures, not in a footnote. Report three ways — bare model, model + scaffold,
full system — with date and versions. A believable number with its flaws
disclosed beats an impressive one nobody can check.

See `INVARIANTS.md` for the pre-commit checklist, the four places where the
build spec's code snippets contradicted its own acceptance tests, and the two
spec instructions that could not be followed as written.
