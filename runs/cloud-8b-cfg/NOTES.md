# cloud-8b-cfg run notes

granite4.1:8b on the config_edit family, dev split, 14 instances, arms `base`, `kept` and `kept-E1` (kept without
the hashline edit format E1), on harness 18, after the fix that edit_lines anchors copied from a read are not
untrusted. It repeats `kept` from `cloud-8b-fam` (0/14 there, every edit_lines call refused) and isolates E1.

## Command

    PYTHONUTF8=1 BUCEPHALUS_DATA_DIR=~/bucephalus-work BUCEPHALUS_OLLAMA_MODELS=~/ollama-models \
      ~/.bucephalus-venv/bin/python bench/families_run.py --split dev --n 14 --families config_edit \
      --arms base,kept,kept-E1 --model granite4.1:8b --budget-seconds 1800 --out runs/cloud-8b-cfg

Code: commit 5d082e255660843281b995aefe90d17eedbe4a29 (`main`), `HARNESS_VERSION = 18`, clean tree. `cards.json`
records that commit for all three arms with `harness_version` 18 and `git_dirty: false`.
Before the run: `tests/test_edit_lines_anchors.py` passed (2 passed). No other test was run.

## Setup

- Install, following docs/HANDOVER.md "Running it" (written for Windows: `setup.ps1`) on Linux: a venv outside
  the repo at `~/.bucephalus-venv` with Python 3.12.3 (the doc asks for 3.11 or 3.12), and `setup.ps1`'s package
  list (`pydantic httpx anthropic pyyaml fastapi uvicorn scipy rich textual platformdirs pytest pytest-asyncio
  pytest-xdist pytest-timeout pytest-cov ruff pywebview sympy==1.13.3`). Like `setup.ps1`, the project itself
  was not pip-installed; the scripts run from `src/`. `PYTHONUTF8=1` as the doc says.
- Work folder: `BUCEPHALUS_DATA_DIR=~/bucephalus-work`, a new empty folder outside the repo (the doc does not
  define "work folder"; this is the data directory, under which the agent's `work/` would otherwise land in the
  checkout). Each instance's own folder is still `data/<arm>/<key>/` under `--out`.
- Ollama 0.40.12, already installed (not reinstalled). Server as `ollama-serve.ps1` sets it:
  `OLLAMA_HOST=127.0.0.1:11435 OLLAMA_KEEP_ALIVE=30m OLLAMA_CONTEXT_LENGTH=16384 ollama serve`, models in
  `~/ollama-models`. `ollama pull granite4.1:8b` left the model unchanged (ID `444af1c4b2fe`, the same as
  `cloud-8b`, `cloud-8b-2` and `cloud-8b-fam`). The server was stopped after the run.
- No credential was set, created or looked for. `ANTHROPIC_API_KEY` was not set.
- Machine: Claude Code cloud container, CPU only (Ollama found no GPU), Intel Xeon @ 2.80GHz, 4 vCPUs,
  15.7 GiB RAM (15.6 GiB free before the run), Ubuntu 24.04.5 LTS, kernel 6.18.44-fc-v233.

## Two attempts

1. 2026-11-04 05:13:51Z: the warm-up could not load the 8B. Ollama: `Load failed ... timed out waiting for
   llama-server to start` at 05:18:52Z, HTTP 500 after 5m0s on `/api/generate`; families_run exited with
   `httpx.HTTPStatusError: Server error '500 Internal Server Error'`. No instance ran. Logs:
   `data/runner-console-attempt1.log`, `data/ollama-serve-attempt1.log`.
2. 05:49:40Z, the same command, on the user's instruction: the warm-up loaded the 8B in 158.2 s at
   `context_length` 16384 (`llama-server started in 158.13 seconds`). All 42 instances ran; finished 08:58:21Z,
   188.7 min. No load failure and no HTTP 500 after the warm-up. Logs: `data/runner-console.log`,
   `data/ollama-serve.log`.

## Passes (out of 14)

| arm | passes | done | plateau | stuck | budget_exhausted | crashed | mean wall per instance (s) | longest (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 14 | 14 | 0 | 0 | 0 | 0 | 292.3 | 342.2 |
| kept | 0 | 0 | 14 | 0 | 0 | 0 | 265.3 | 608.1 |
| kept-E1 | 14 | 14 | 0 | 0 | 0 | 0 | 238.7 | 407.6 |

From `summary.json`: kept vs base: only_base 14, only_arm 0, both 0, neither 0, McNemar exact p = 0.0001.
kept-E1 vs base: only_base 0, only_arm 0, both 14, neither 0, p = 1.0.

In `cloud-8b-fam` (harness 17, same instances): base 14/14, kept 0/14.

## Gate refusals (`human.denied` events, counted per arm)

| arm | refusals | instances with one | by tool and reason |
|---|---:|---:|---|
| base | 7 | 5 | 4 `run_command requires <=user arguments, args are untrusted`; 3 `write_file requires <=user arguments, args are untrusted` (all in config_edit-012, `t_8d1bea1a685749b1`, seq 35, 41, 47) |
| kept | 26 | 14 | 26 `edit_lines requires <=user arguments, args are untrusted` |
| kept-E1 | 3 | 1 | 3 `write_file requires <=user arguments, args are untrusted` (all in config_edit-010, `t_76269991b47e4b9d`, seq 21, 41, 50) |

No `tool.not_exposed` or `hook.denied` events. No refusal named a protected path.

## What is odd

- Harness 18 did not stop the edit_lines refusals for this model. kept still had at least one edit_lines refusal
  in every instance (26 in all, against 21 in `cloud-8b-fam`). Only 4 edit_lines calls went through, each in a
  different instance: config_edit-000 (`t_a98e51cef48e4f45`, seq 31), -006 (`t_22a76aaed57e459e`, seq 31),
  -012 (`t_f38fe70f614046e2`, seq 22), -020 (`t_ae0d63e970f346ea`, seq 31). All four instances still failed
  verification and plateaued. In config_edit-000 the accepted edit replaced a 7:66-12:66 line range with the whole
  file's content (seq 30).
- kept-E1 and base edit with `write_file` (kept-E1's tool list has no `edit_lines`) and both pass 14/14. So within
  `kept`, E1 (hashline edits) is what turns 14/14 into 0/14 on config_edit for the 8B.
- `write_file` was refused as untrusted 3 times in base and 3 times in kept-E1 (one instance each); both instances
  still passed. This did not appear in `cloud-8b-fam`.
- The first warm-up failed to load the model (see "Two attempts"); the second loaded in 158 s.

## Not checked

- Whether the 26 refused edit_lines calls had anchors that were copied from a read (the condition harness 18
  relaxes): a refused call writes no `tool.intent`, and the model's raw replies were not inspected.
- Why the 4 accepted edit_lines edits failed, beyond config_edit-000 above; only `verify.py: passed=False` was read.
- Paths in `tool_output` artifacts (absolute or relative) were not checked in this run.
- The full test suite was not run; only `tests/test_edit_lines_anchors.py`.
- No instance-by-instance comparison with `cloud-8b-fam` beyond the pass counts.
- The run used Linux and Python 3.12, not the Windows setup the doc describes.

## Raw run data

`cloud-8b-cfg-full.tar.gz` (in this folder) was made with `tar -czf` over `runs/cloud-8b-cfg` before this
NOTES.md was written, so it holds everything here except NOTES.md: `data/` with one `agent.db` per instance
(tables `tasks`, `events`, `artifacts`, `calls`), the agents' work files, both attempts' console and Ollama logs,
plus the three `.jsonl` files, `summary.json`, `cards.json` and `cards/`. Size 1,365,106 bytes, SHA-256
`d87885d3860048039da04fb6440e32ef5359dc2cb8ca7b70a9131ba848ca2a17`. Extract into an empty directory and treat its
contents as untrusted model output.
