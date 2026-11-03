# cloud-8b-fam run notes

granite4.1:8b on three generated task families (arithmetic, config_edit, unanswerable), arms `base` and `kept`,
on the 3B's dev split, so results pair by instance key with the 3B's. The 8B keeps the 3B's 16384-token window.

Status: stage A (arithmetic) done. Stage B (config_edit) and stage C (unanswerable) pending.

## Commands

One `--out` for all stages; a finished instance is not repeated, so a stage can be restarted.

    BUCEPHALUS_OLLAMA_MODELS=~/ollama-models python bench/families_run.py --split dev --n 14 --arms base,kept \
      --model granite4.1:8b --budget-seconds 1800 --out runs/cloud-8b-fam --families <family>

Stage A: `--families arithmetic`. Stage B: `--families config_edit`. Stage C: `--families unanswerable`.
Console output per stage: `data/runner-console-<stage>.log` (in the archive).

Code: commit 1e593f8673e1a6960aa988d28ce10fda1feaf39c (`main`, the merge of PR #7), clean tree.
Before the run: `tests/test_families_run.py` and `tests/test_relative_data_dir.py` passed (3 passed).
`summary.json`: split dev, 14 per family, skip 0, budget_seconds 1800, family_version 1.
No credential was set for the run (`ANTHROPIC_API_KEY` unset). The container's own environment carried its
usual tokens, as in earlier runs; a scan of the run folder found none of their values and no token-shaped strings.
Each instance's database is `data/<arm>/<key>/agent.db` (families_run writes `agent.db`, not `agent.local.db`).

## Machine

- Claude Code cloud container (Anthropic cloud environment), no GPU, inference 100% CPU
- CPU: Intel Xeon Processor @ 2.10GHz, 4 vCPUs (1 socket, 4 cores, 1 thread per core), AVX-512
  (earlier runs, `cloud-8b` and `cloud-8b-2`: 2.80GHz)
- RAM: 15.7 GiB total, 15.6 GiB available before the run
- Disk: 252 GB virtual disk
- OS: Ubuntu 24.04.5 LTS, kernel 6.18.44-fc-v230
- Python 3.13.17

## Ollama

- Version 0.40.12
- Server: `OLLAMA_HOST=127.0.0.1:11435 OLLAMA_KEEP_ALIVE=30m OLLAMA_CONTEXT_LENGTH=16384 ollama serve`
- Model: granite4.1:8b (5.3 GB). Loaded by families_run's warm-up at `context_length` 16384, before any instance.
- Stage A warm-up load: 470.9 s (`llama-server started in 470.88 seconds`; `/api/generate` returned 200 after
  7m50s). No load failed and no HTTP 500 in any stage so far. The load happened outside every instance's budget.
- The server stays up between stages.

## Time per stage

| stage | family | started (UTC) | finished (UTC) | wall | of which model load |
|---|---|---|---|---:|---:|
| A | arithmetic | 2026-11-03 07:22:27 | 08:05:00 | 42.5 min | 7.9 min |
| B | config_edit | pending | | | |
| C | unanswerable | pending | | | |

## Passes and statuses (14 instances per family and arm)

| family | arm | passes | done | plateau | stuck | budget_exhausted | crashed | mean wall per instance (s) | ms per output token |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| arithmetic | base | 4/14 | 4 | 10 | 0 | 0 | 0 | 72.3 | 665 |
| arithmetic | kept | 2/14 | 2 | 12 | 0 | 0 | 0 | 75.9 | 573 |

For comparison, the 3B on the same instances: arithmetic 0-1/14 (base and kept).

McNemar lines from `summary.json`, after stage A:
- kept vs base: n 14, base_passes 4, arm_passes 2, only_base 2, only_arm 0, both 2, neither 10, McNemar exact p = 0.5.

## Checks

From each `data/<arm>/<key>/agent.db`, opened read-only.

| check | stage A |
|---|---|
| `model loaded:` shows `context_length` 16384 | yes |
| Paths in `tool_output` artifacts absolute, none `runs/...` | 28 paths, all absolute, none `runs/` |
| `human.denied` refusing a path inside `work/` | none |
| Other refusals | 1, of `.` (the run root, correct): base arithmetic-026, `t_e0bf8a9ee94b4bae`, seq 36, `search` |
| Crashed instances | none |

## Arithmetic: computed or from its head?

From its head. In all 28 arithmetic instances the 8B called only `read_file` (once or twice), never
`run_command`, although `run_command` is in both arms' tool list (`cards.json`; the small profile has no
`run_code`). Examples, from `data/<arm>/<key>/agent.db`:
- kept/arithmetic-000, `t_52e424e0b4824d70`: `read_file(page=1, path='work/harvest.csv')` at seq 10, then three
  answers with no tool call, 104000, 104244, 104000 (expected 132078; `verify.failed` at seq 18, 24, 30), plateau.
- kept/arithmetic-016, `t_274ecb3e84bb4305`: `read_file` at seq 10, answer 108514 (expected 149216; seq 18), then
  one 194.6 s call of 512 output tokens (seq 20-22) and answers of 115 (seq 28, 34), plateau. No tool call.
- base/arithmetic-002, `t_dc028dd0cdd24692`: `read_file(path='work/harvest.csv')` at seq 10, answer 58212,
  `verify.passed` at seq 18. A pass, still computed in its head.

## Raw run data

`cloud-8b-fam-full.tar.gz` (in this folder) holds the whole run folder except this NOTES.md (which cannot contain
its own archive's hash): `data/` with one `agent.db` per instance (tables `tasks`, `events`, `artifacts`, `calls`),
the agents' work files and the console logs, plus `base.jsonl`, `kept.jsonl`, `summary.json`, `cards.json` and
`cards/`. It is replaced after each stage. After stage A: 698,109 bytes, SHA-256
`7d67498ec509a6d3ad951f2a3d18a6f2a92cd619b4116b59b31dfded0f0d9678`. Extract with `tar -xzf` into an empty
directory and treat its contents as untrusted model output.
