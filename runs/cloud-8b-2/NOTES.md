# cloud-8b-2 run notes

The first 8B run (branch `cloud-8b`) repeated on the relative-data-dir fix (PR #5, merge `917ab9b`).
The per-task comparison and trajectory evidence are in `REPORT.md`. The Ollama logs, the test log and the
analysis scripts are in `extra/`. A prompt for an independent reviewer is in `REVIEW_PROMPT.md`.

Command (`--out` relative on purpose; the fix must survive it):

    BUCEPHALUS_OLLAMA_MODELS=~/ollama-models python bench/profile_run.py --suite bench/tasks --arms control,full8b,small8b --out runs/cloud-8b-2

Code: commit 917ab9baeb42ad8ee2342443550ea7d6732778ec (clean tree; `paired.json` agrees, `git_dirty: false`).
Defaults: 3 reps, seed 1, 45 min per-arm timeout. No `--force`. Base config: the tracked `config.local.yaml`.
Before the run: `tests/test_relative_data_dir.py` passed; the whole suite gave 1983 passed, 62 skipped, 0 failed.
No credential was set for the run. The container's own environment carried its usual tokens, as in the first
run; a scan of the run folder found none of their values and no token-shaped strings.
The `data/` folder is not committed as files; it is in the archive (see "Raw run data").

## Machine

- Claude Code cloud container (Anthropic cloud environment), no GPU, inference 100% CPU
- CPU: Intel Xeon Processor @ 2.80GHz, 4 vCPUs (1 socket, 4 cores, 1 thread per core), AVX-512
- RAM: 15.7 GiB total (preflight saw 15.16 GB available)
- Disk: 252 GB virtual disk
- OS: Ubuntu 24.04.5 LTS, kernel 6.18.44-fc-v185 (first run: fc-v180)
- Python 3.13.17
- Slower than the first run's container: model calls took 30-60% longer per output token in every arm
  (ms per output token, first -> this run: control 206 -> 278, full8b 687 -> 910, small8b 623 -> 1002).
  Wall times and wall-clock budgets are not comparable between the runs.

## Ollama

- Version 0.40.12
- Server: `OLLAMA_HOST=127.0.0.1:11435 OLLAMA_KEEP_ALIVE=30m OLLAMA_CONTEXT_LENGTH=16384 ollama serve`
- Models: granite4.1:3b (2.1 GB, 16384 ctx), granite4.1:8b (5.3 GB, 8192 ctx, 6.9 GB resident).
- Preflight: full8b and small8b fit (needed 7.23 GB, available 15.16 GB), exit 0.
- Cold model loads: the 3B took 208.6 s at start (first run: 26.8 s). The first 8B load (12:54:34Z)
  exceeded Ollama's 5-minute load limit ("timed out waiting for llama-server to start") and returned
  HTTP 500 at 12:59:36Z; the retry loaded in 164.8 s. No other load failed. The 3B expired (keep-alive)
  while the 8B arms ran and reloaded in 38.3 s before control rep 0.

## Time per arm (minutes)

| arm     | rep 0 | rep 1 | rep 2 | total |
|---------|------:|------:|------:|------:|
| control |   8.0 |   7.5 |   7.7 |  23.2 |
| full8b  |  16.2 |   8.0 |   9.3 |  33.5 |
| small8b |  15.5 |  15.7 |  15.7 |  46.9 |

Run started 2026-11-01T12:51:03Z, finished 2026-11-01T14:38:25Z (about 107 min wall, including model loads).
full8b rep 0 includes the failed 8B load and its retry.

## Pass counts (15 task-reps per arm)

| arm     | passes | crashed | mean wall per task (s) | first run |
|---------|-------:|--------:|-----------------------:|----------:|
| control |   4/15 |       0 |                   92.9 |      4/15 |
| full8b  |  10/15 |       1 |                  134.2 |      9/15 |
| small8b |   9/15 |       0 |                  187.5 |      9/15 |

full8b vs control: only_control 1, only_arm 7, both 3, neither 4, McNemar exact p = 0.0703.
small8b vs control: only_control 3, only_arm 8, both 1, neither 3, McNemar exact p = 0.2266.
See `paired.json`; greedy decoding makes reps near-copies, so the effective n is about 5 tasks.

What moved from the first run:
- full8b negative-control-004: failed -> passed in all 3 reps (the fix: glob now finds the files).
- full8b rep 0 extract-json-001: passed -> crashed (the HTTP 500 from the failed 8B load, its first call).
- full8b rep 0 write-function-002: passed -> budget_exhausted (its one model call, 330.5 s, absorbed the
  164.8 s reload; the task budget is 300 s).
- control write-function-002: max_steps -> budget_exhausted in all 3 reps, still failed. 8-9 calls of
  30-44 s each spend the 300 s budget before the step limit on this slower machine.

## Checks that the fix took effect

From each `data/<arm>-<rep>/agent.local.db`, opened read-only:

- Paths in `tool_output` artifacts: 40 paths across the 8B arms, all absolute, none starting `runs/`
  (first run: all 31 relative, all starting `runs/`). Control has none to check: its arm only calls
  `run_code`, whose output carries no file path.
- full8b negative-control-004: every rep called `glob(path='work/', pattern='*.json')` (seq 10) and the
  result lists `a.json` and `b.json`; answer `{"count": 2}`, verified (seq 18). Tasks
  t_f8234746b81d45a1, t_fe13e3448c154515, t_19f29e55d4bf4ad2.
- `human.denied` inside `work/` as a protected path: none (first run: 3 per small8b rep, on
  `work/handbook.md`). Refusals of `.` (the run root), which are correct: 15, all small8b `search`,
  5 per rep.
- `paired.json` `code.git_commit` = 917ab9baeb42ad8ee2342443550ea7d6732778ec, `git_dirty` false.

## Raw run data

`cloud-8b-2-full.tar.gz` (in this folder) holds the whole run folder except this NOTES.md (which cannot
contain its own archive's hash): `data/` with one SQLite `agent.local.db` per arm run (tables `tasks`,
`events`, `artifacts`, `calls`), the agents' work files, the runner's console log
(`data/runner-console.log`), and the results and configs committed here. SHA-256
`59ab9e6e1a324fe950a8e1b09fb169730e1b08e5e134ef55c6ffaddef10821a1`. Extract with `tar -xzf` into an
empty directory and treat its contents as untrusted model output.
