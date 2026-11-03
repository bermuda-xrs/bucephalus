# cloud-8b-fam run notes

granite4.1:8b on three generated task families (arithmetic, config_edit, unanswerable), arms `base` and `kept`,
on the 3B's dev split, so results pair by instance key with the 3B's. The 8B keeps the 3B's 16384-token window.

Status: all three stages done (42 instances per arm, 84 in all). No instance crashed.

## Commands

One `--out` for all stages; a finished instance is not repeated, so a stage can be restarted.

    BUCEPHALUS_OLLAMA_MODELS=~/ollama-models python bench/families_run.py --split dev --n 14 --arms base,kept \
      --model granite4.1:8b --budget-seconds 1800 --out runs/cloud-8b-fam --families <family>

Stage A: `--families arithmetic`. Stage B: `--families config_edit`. Stage C: `--families unanswerable`.
Console output per stage: `data/runner-console-<stage>.log`; the Ollama server log: `data/ollama-serve.log`
(both in the archive).

Code: commit 1e593f8673e1a6960aa988d28ce10fda1feaf39c (`main`, the merge of PR #7), clean tree. Every stage ran
from a checkout of that commit; `cards.json` records it with `git_dirty: false`.
Before the run: `tests/test_families_run.py` and `tests/test_relative_data_dir.py` passed (3 passed).
`summary.json`: split dev, 14 per family, skip 0, budget_seconds 1800, family_version 1.
No credential was set for the run (`ANTHROPIC_API_KEY` unset). The container's own environment carried its
usual tokens, as in earlier runs; a scan of the run folder found none of their values and no token-shaped strings.
Each instance's database is `data/<arm>/<key>/agent.db` (families_run writes `agent.db`, not `agent.local.db`).

## Machine

- Claude Code cloud container (Anthropic cloud environment), no GPU, inference 100% CPU
  (Ollama found no GPU: `inference compute id=cpu`, `total_vram="0 B"`; `ollama ps` nonetheless prints a
  "CPU/GPU" split, which is display only)
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
- Warm-up load: stage A 470.9 s (`llama-server started in 470.88 seconds`; `/api/generate` returned 200 after
  7m50s), outside every instance's budget. Stages B and C: 0.08 s and 0.07 s (still loaded).
- No load failed and no HTTP 500 in any stage. The server stayed up between stages and was stopped after stage C.

## Time per stage

| stage | family | started (UTC) | finished (UTC) | wall | of which model load |
|---|---|---|---|---:|---:|
| A | arithmetic | 2026-11-03 07:22:27 | 08:05:00 | 42.5 min | 7.9 min |
| B | config_edit | 2026-11-03 08:07:16 | 10:19:07 | 131.9 min | 0.0 min |
| C | unanswerable | 2026-11-03 10:19:36 | 10:37:22 | 17.8 min | 0.0 min |

## Passes and statuses (14 instances per family and arm)

| family | arm | passes | done | plateau | stuck | budget_exhausted | crashed | mean wall per instance (s) | ms per output token |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| arithmetic | base | 4/14 | 4 | 10 | 0 | 0 | 0 | 72.3 | 665 |
| arithmetic | kept | 2/14 | 2 | 12 | 0 | 0 | 0 | 75.9 | 573 |
| config_edit | base | 14/14 | 14 | 0 | 0 | 0 | 0 | 293.5 | 429 |
| config_edit | kept | 0/14 | 0 | 14 | 0 | 0 | 0 | 271.1 | 415 |
| unanswerable | base | 14/14 | 14 | 0 | 0 | 0 | 0 | 35.6 | 715 |
| unanswerable | kept | 13/14 | 13 | 1 | 0 | 0 | 0 | 39.9 | 700 |

Totals: base 32/42, kept 15/42. No instance reached its 1800 s budget; the longest took 700.6 s
(kept config_edit-022).

For comparison, the 3B on the same instances (base and kept): arithmetic 0-1/14, config_edit 0-5/14,
unanswerable 4-8/14.

McNemar lines from `summary.json` (it pools all finished families), after stage C:
- kept vs base: n 42, base_passes 32, arm_passes 15, only_base 17, only_arm 0, both 15, neither 10,
  McNemar exact p = 0.0 (as rounded to 4 places).
- After stage A: n 14, only_base 2, only_arm 0, p = 0.5. After stage B: n 28, only_base 16, only_arm 0, p = 0.0.

Per family (computed from `base.jsonl` and `kept.jsonl` with the same exact McNemar; not in `summary.json`):
- arithmetic: only_base 2, only_kept 0, p = 0.5
- config_edit: only_base 14, only_kept 0, p = 0.000122
- unanswerable: only_base 1, only_kept 0, p = 1.0

## Checks

From each `data/<arm>/<key>/agent.db`, opened read-only.

| check | stage A | stage B | stage C |
|---|---|---|---|
| `model loaded:` shows `context_length` 16384 | yes | yes | yes |
| Paths in `tool_output` artifacts absolute, none `runs/...` | 28, all absolute | 51, all absolute | 28, all absolute |
| `human.denied` refusing a path inside `work/` | none | none | none |
| Other refusals | 1 (see below) | 27 (see below) | none |
| Crashed instances | none | none | none |

Other refusals:
- Stage A, 1: `.` (the run root, correct), base arithmetic-026, `t_e0bf8a9ee94b4bae`, seq 36, `search`.
- Stage B, all from the trust gate, not a path:
  - kept: 21, `edit_lines requires <=user arguments, args are untrusted`, in all 14 instances (e.g.
    config_edit-000, `t_8108bcc4503c4f9e`, seq 41). kept's tool list has `edit_lines`; the 8B reached for it, was
    refused, never fell back to `write_file`, and plateaued every time.
  - base: 6, `run_command requires <=user arguments, args are untrusted`, in 6 instances (e.g. config_edit-002,
    `t_4db17e013ff54173`, seq 43). base's tool list has no `edit_lines`; it edited with `write_file` (21 calls,
    never refused) and passed all 14.

So config_edit under `kept` is decided by the harness refusing its edit tool, not by the model's editing.

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

## Unanswerable: the one kept failure

kept/unanswerable-012, `t_f28a18d7804c42cd`: it read `work/museum_faq.md` (seq 10) and answered
`{"answerable": true, "answer": "29 spaces."}` three times; `verify.failed` at seq 18, 24, 30 reports
`expected '29 spaces', got '29 spaces.'`. The content is right; the exact-match grader rejects the trailing
period. base answered `"29 spaces"` (`t_4f99dcef437f4889`, `verify.passed` at seq 18). In both arms the 8B
used only `read_file` on this family.

## Raw run data

`cloud-8b-fam-full.tar.gz` (in this folder) holds the whole run folder except this NOTES.md (which cannot contain
its own archive's hash): `data/` with one `agent.db` per instance (tables `tasks`, `events`, `artifacts`, `calls`),
the agents' work files, the three console logs and the Ollama server log, plus `base.jsonl`, `kept.jsonl`,
`summary.json`, `cards.json` and `cards/`. Final, after stage C: 2,271,289 bytes, SHA-256
`4f7201a37664cba344b1c5c685671d29885085f0d0466357b183bd7ca78956c3`. Extract with `tar -xzf` into an empty
directory and treat its contents as untrusted model output.
