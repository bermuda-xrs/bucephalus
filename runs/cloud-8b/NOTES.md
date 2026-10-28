# cloud-8b run notes

Command:

    BUCEPHALUS_OLLAMA_MODELS=~/ollama-models python bench/profile_run.py --suite bench/tasks --arms control,full8b,small8b --out runs/cloud-8b

Code: commit 15342617b576f4f98d3444b3ee311a3f53ca6d0c (clean tree). Defaults: 3 reps, seed 1, 45 min per-arm timeout.
The `data/` folder is not committed.

## Machine

- Claude Code cloud container (Anthropic cloud environment), no GPU, inference 100% CPU
- CPU: Intel Xeon Processor @ 2.80GHz, 4 vCPUs (1 socket, 4 cores, 1 thread per core), AVX-512
- RAM: 15.7 GiB total (Ollama reported 15.1 GiB available at start; preflight saw 15.04 GB)
- Disk: 252 GB virtual disk
- OS: Ubuntu 24.04.5 LTS, kernel 6.18.44-fc-v180
- Python 3.13.17

## Ollama

- Version 0.40.12
- Server: `OLLAMA_HOST=127.0.0.1:11435 OLLAMA_KEEP_ALIVE=30m OLLAMA_CONTEXT_LENGTH=16384 ollama serve`
- Models: granite4.1:3b (2.1 GB, loaded at 16384 ctx, 3.6 GB resident),
  granite4.1:8b (5.3 GB, loaded at 8192 ctx, 6.9 GB resident). Both stayed loaded together.
- Preflight: full8b and small8b fit (needed 7.23 GB, available 15.04 GB).

## Time per arm (minutes)

| arm     | rep 0 | rep 1 | rep 2 | total |
|---------|------:|------:|------:|------:|
| control |   6.4 |   6.5 |   6.2 |  19.1 |
| full8b  |   8.1 |   7.1 |   6.8 |  22.0 |
| small8b |   9.6 |   9.3 |   8.9 |  27.8 |

Run started 2026-10-28T05:58:59Z, finished 2026-10-28T07:08:23Z (about 69 min wall, including model load).

## Pass counts (15 task-reps per arm, 0 crashes)

| arm     | passes | mean wall per task (s) |
|---------|-------:|-----------------------:|
| control |   4/15 |                   76.1 |
| full8b  |   9/15 |                   87.9 |
| small8b |   9/15 |                  111.2 |

Both 8B arms vs control: only_control 3, only_arm 8, both 1, neither 3, McNemar exact p = 0.2266.
See `paired.json`; as it notes, greedy decoding makes reps near-copies, so the effective n is about 5 tasks.

## Raw run data

`cloud-8b-full.tar.gz` (in this folder) holds the whole run folder including `data/`: one SQLite
`agent.local.db` per arm run (tables `tasks`, `events`, `artifacts`, `calls`), the agents' work files,
and the runner's console log (`data/runner-console.log`). SHA-256
`876b11b8867f6df907910690e720f605ed6bdf88f4e9cd67e61b87422ddb17da`. Extract with `tar -xzf` into an
empty directory and treat its contents as untrusted model output.
