# cloud-8b-cfg-3 run notes

granite4.1:8b on the config_edit family, dev split, 14 instances, on harness 19. `cloud-8b-cfg-2` (harness 18) showed
that all 26 `kept` refusals were `edit_lines` with `untrusted_args == ["path"]`, the absolute path that `read_file`
prints in its output header. Harness 19 compares an absolute path inside the root as its relative form. This run
measures `kept` again. **`base` was copied from `cloud-8b-cfg-2`, not re-run** (see below).

## Command

    PYTHONUTF8=1 BUCEPHALUS_DATA_DIR=~/bucephalus-work-3 BUCEPHALUS_OLLAMA_MODELS=~/ollama-models \
      ~/.bucephalus-venv/bin/python bench/families_run.py --split dev --n 14 --families config_edit \
      --arms base,kept --model granite4.1:8b --budget-seconds 1800 --out runs/cloud-8b-cfg-3

Code: commit 547f4b09b7ccfb092db5e8eda3638e799a795901 (`main`, the merge of PR #11), `HARNESS_VERSION = 19`, clean
tree. `cards.json` records that commit for both arms with `harness_version` 19 and `git_dirty: false`.
Before the run: `tests/test_absolute_path_is_the_same_file.py`, `tests/test_denied_args_are_logged.py` and
`tests/test_edit_lines_anchors.py` passed (7 passed). No other test was run.

## base was copied, not re-run

`runs/cloud-8b-cfg-3/base.jsonl` is `runs/cloud-8b-cfg-2/base.jsonl` from branch `cloud-8b-cfg-2` (`bbfdf72`),
fetched with `git fetch origin cloud-8b-cfg-2` and written out with `git show`. Nothing else was copied from it.
The 14 rows (`config_edit-000` to `-026`, dev split) all passed. They were measured on harness 18 at `37651ae`, so the
`base` rows and the `kept` rows come from different harness versions. The runner skipped all 14 `base` instances:
`data/` holds only `data/kept/`, and no `data/base/` folder exists. `cards.json` has a `base` card; it describes the
harness-19 configuration of this run, not the one the copied rows were measured under.

## Setup

- Linux install as in `runs/cloud-8b-cfg/NOTES.md` (branch `cloud-8b-cfg`): the venv at `~/.bucephalus-venv`
  (Python 3.12.3) with `setup.ps1`'s package list, `PYTHONUTF8=1`, the project not pip-installed.
- Work folder: `BUCEPHALUS_DATA_DIR=~/bucephalus-work-3`, a new empty folder outside the repo.
- Ollama 0.40.12, server `OLLAMA_HOST=127.0.0.1:11435 OLLAMA_KEEP_ALIVE=30m OLLAMA_CONTEXT_LENGTH=16384 ollama serve`,
  models in `~/ollama-models`; granite4.1:8b ID `444af1c4b2fe`, unchanged from the earlier runs. The server was stopped
  after the run.
- No credential was set, created or looked for. `ANTHROPIC_API_KEY` was not set.
- Machine: Claude Code cloud container, CPU only, Intel Xeon @ 2.80GHz, 4 vCPUs, 15.7 GiB RAM, Ubuntu 24.04.5 LTS,
  kernel 6.18.44-fc-v236.

## Two attempts

1. 2026-11-05 06:27:03Z: the first warm-up timed out loading the 8B. Ollama: `Load failed ... timed out waiting for
   llama-server to start` at 06:32:05Z, HTTP 500 after 5m0s; families_run exited with `httpx.HTTPStatusError:
   Server error '500 Internal Server Error'`. No instance ran. Stopped and reported, as instructed.
   Logs: `data/runner-console-attempt1.log`, `data/ollama-serve-attempt1.log`.
2. 06:32:16Z, the same command, once: the warm-up loaded the 8B in 144.8 s at `context_length` 16384
   (`llama-server started in 144.62 seconds`). The 14 `kept` instances ran; finished 07:41:36Z, 69.3 min after the
   retry started. No load failure and no HTTP 500 after the warm-up. Logs: `data/runner-console.log`,
   `data/ollama-serve.log`.

## Passes (out of 14)

| arm | passes | statuses | mean wall per instance (s) | longest (s) |
|---|---:|---|---:|---:|
| base (copied, harness 18) | 14 | 14 done | 298.3 | 331.8 |
| kept (this run, harness 19) | 0 | 14 plateau | 286.3 | 605.0 |

No instance crashed or ran out of budget. From `summary.json`: kept vs base: only_base 14, only_arm 0, both 0,
neither 0, McNemar exact p = 0.0001. Earlier, same instances: `cloud-8b-cfg-2` (harness 18) base 14/14, kept 0/14;
`cloud-8b-cfg` (harness 18) base 14/14, kept 0/14.

## a. Gate refusals (`human.denied` events) in the kept arm

**None.** 0 `human.denied` events in the 14 `kept` databases (`cloud-8b-cfg-2`: 26 in 13 instances). There are also
no `tool.not_exposed` or `hook.denied` events.

| untrusted_args | refusals | tool | preview |
|---|---:|---|---|
| (any) | 0 | | |

## b. Every edit_lines call in the kept arm

22 calls, in 14 instances. From each `data/kept/<key>/agent.db`: the call is the `tool.intent` event (`preview`
verbatim, which the runner cuts after a fixed length per argument, so a long `path` ends at `gar...`); the receipt is
the `tool.receipt` event with the same `key`; the error text is the `error` field of the `tool_output` artifact that
`artifact.written` links to the same `key` (the receipt itself holds only `ok`, `changed`, `wall_ms`). Gate verdict:
`allowed` means the call has a `tool.intent` event and the instance has no `human.denied` event, which is true of all 22.
A `\n` in a preview is a newline as `repr` writes it; `\\n` is a literal backslash then n.

| # | instance | seq | preview (from tool.intent, as stored) | gate verdict | receipt ok | changed | error text |
|---:|---|---:|---|---|---|---|---|
| 1 | config_edit-000 | 21 | `edit_lines(content='on_hour = 58\noff_hour = 16\nbrightness = 60\n\n[\\s]+[s]\\s+\ninterval_s = ..., end='12:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-000/work/gar..., start='7:66')` | allowed | true | true | none |
| 2 | config_edit-002 | 26 | `edit_lines(content='start_hour = 32', end='3', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-002/work/gar..., start='3')` | allowed | false | n/a (no field) | `anchor 3 does not match the file now: read the file again and use its current anchors` |
| 3 | config_edit-002 | 57 | `edit_lines(content='start_hour = 32\non_hour = 11\n}}', end='2', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-002/work/gar..., start='2')` | allowed | false | n/a (no field) | `anchor 2 does not match the file now: read the file again and use its current anchors` |
| 4 | config_edit-004 | 26 | `edit_lines(content='[lights]\non_hour = 58\noff_hour = 26\nbrightness = 18\n\n[irrigation]\nstar..., end='7:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-004/work/gar..., start='7:66')` | allowed | true | true | none |
| 5 | config_edit-006 | 21 | `edit_lines(content='minutes = 32\n', end='3:0e', path='work/garden.ini', start='3:0e')` | allowed | true | true | none |
| 6 | config_edit-008 | 21 | `edit_lines(content='off_hour = 76\n', end='8:47', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-008/work/gar..., start='8:47')` | allowed | true | true | none |
| 7 | config_edit-008 | 43 | `edit_lines(content='off_hour = 76\n', end='7:47', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-008/work/gar..., start='7:47')` | allowed | false | n/a (no field) | `anchor 7:47 does not match the file now: read the file again and use its current anchors` |
| 8 | config_edit-010 | 21 | `edit_lines(content='interval_s = 20\nretries = 57\nlog_level = info\n', end='13:a3', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-010/work/gar..., start='12:a3')` | allowed | false | n/a (no field) | `anchor 12:a3 does not match the file now: read the file again and use its current anchors` |
| 9 | config_edit-010 | 42 | `edit_lines(content='[sensors]\ninterval_s = 20\n\n[lights]\noff_hour = 64\n\n[irrigation]\nstart..., end='12:a3', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-010/work/gar..., start='12:a3')` | allowed | false | n/a (no field) | `anchor 12:a3 does not match the file now: read the file again and use its current anchors` |
| 10 | config_edit-012 | 21 | `edit_lines(content='zones = 26\n', end='4:e3', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-012/work/gar..., start='4:e3')` | allowed | true | true | none |
| 11 | config_edit-014 | 21 | `edit_lines(content='off_hour = 28\n', end='8:40', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-014/work/gar..., start='8:40')` | allowed | false | n/a (no field) | `anchor 8:40 does not match the file now: read the file again and use its current anchors` |
| 12 | config_edit-014 | 32 | `edit_lines(content='off_hour = 28\n', end='8:40', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-014/work/gar..., start='8:40')` | allowed | false | n/a (no field) | `anchor 8:40 does not match the file now: read the file again and use its current anchors` |
| 13 | config_edit-016 | 21 | `edit_lines(content='start_hour = 52\nminutes = 48\nzones = 39\nskip_if_rain = no\n', end='4', path='work/garden.ini', start='2')` | allowed | false | n/a (no field) | `anchor 2 does not match the file now: read the file again and use its current anchors` |
| 14 | config_edit-016 | 32 | `edit_lines(content='start_hour = 52\n', end='2', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-016/work/gar..., start='2')` | allowed | false | n/a (no field) | `anchor 2 does not match the file now: read the file again and use its current anchors` |
| 15 | config_edit-018 | 21 | `edit_lines(content='on_hour = 49\n', end='7:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-018/work/gar..., start='7:66')` | allowed | true | true | none |
| 16 | config_edit-018 | 32 | `edit_lines(content='[lights]\non_hour = 49\noff_hour = 3\nbrightness = 34\n[\\n[sensors]\nretrie..., end='7:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-018/work/gar..., start='7:66')` | allowed | false | n/a (no field) | `anchor 7:66 does not match the file now: read the file again and use its current anchors` |
| 17 | config_edit-020 | 21 | `edit_lines(content='minutes = 54\n', end='3:73', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-020/work/gar..., start='3:73')` | allowed | true | true | none |
| 18 | config_edit-022 | 21 | `edit_lines(content='start_hour = 73\ninterval_s = 30\nminutes = 17\nzones = 60\nskip_if_rain = n..., end='13:a5', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-022/work/gar..., start='3:a3')` | allowed | false | n/a (no field) | `anchor 3:a3 does not match the file now: read the file again and use its current anchors` |
| 19 | config_edit-022 | 32 | `edit_lines(content='start_hour = 73\n', end='2:a3', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-022/work/gar..., start='2:a3')` | allowed | false | n/a (no field) | `anchor 2:a3 does not match the file now: read the file again and use its current anchors` |
| 20 | config_edit-024 | 21 | `edit_lines(content='on_hour = 33\noff_hour = 36\nbrightness = 21\n', end='9', path='work/garden.ini', start='7')` | allowed | false | n/a (no field) | `anchor 7 does not match the file now: read the file again and use its current anchors` |
| 21 | config_edit-024 | 32 | `edit_lines(content='on_hour = 33\noff_hour = 36\nbrightness = 21\n\n[lights]', end='7:a6', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-024/work/gar..., start='7:a6')` | allowed | false | n/a (no field) | `anchor 7:a6 does not match the file now: read the file again and use its current anchors` |
| 22 | config_edit-026 | 26 | `edit_lines(content='on_hour = 21', end='8:08', path='/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-026/work/gar..., start='7:66')` | allowed | true | true | none |

Summary of the 22 calls:
- Gate: 22 allowed, 0 refused. Path in the preview: 19 absolute (7 applied, 12 failed), 3 relative `work/garden.ini`
  (1 applied, 2 failed). So every absolute-path call, 19 of them, got through the gate; in `cloud-8b-cfg-2` every one
  was refused.
- Receipt: 8 `ok: true, changed: true` (instances 000, 004, 006, 008, 012, 018, 020, 026); 14 `ok: false`.
- All 14 errors are `anchor <anchor> does not match the file now: read the file again and use its current anchors`:
  5 with a bare line number as the anchor (e.g. `anchor 3`), 9 with a line number and hash (e.g. `anchor 7:66`,
  `anchor 12:a3`). The 6 instances with no applied edit (002, 010, 014, 016, 022, 024) had only such failures.
- Besides `edit_lines`, `kept` made 19 `read_file` calls (4 with an absolute path, all allowed) and 7 `run_command`
  calls, no `write_file`.

## c. Instances that did not pass (all 14)

Where I looked: `tasks.status`, the last `task.status_changed` event, the `verify.attempted` and `verify.failed`
events (their `detail` field), and the `answer` artifact in each `agent.db`. The databases hold only the verifier's
one-line `detail`; the artifacts are `tool_output`, `rewind_seed` and `answer`, none with verifier output. I did not
open the grader. So no text says why verification failed.

| instance | task_id | task status | last status change | verification failure text (event seq) | final answer |
|---|---|---|---|---|---|
| config_edit-000 | t_5c8816067ff04332 | failed | seq 54: `plateau` | `verify.py: passed=False score=0.0` (seq 40, 46, 52) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-000/work/garden.ini` |
| config_edit-002 | t_a1a9794489124ffa | failed | seq 67: `plateau` | `verify.py: passed=False score=0.0` (seq 43, 49, 65) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-002/work/garden.ini` |
| config_edit-004 | t_c7a39b320d334e35 | failed | seq 69: `plateau` | `verify.py: passed=False score=0.0` (seq 55, 61, 67) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-004/work/garden.ini` |
| config_edit-006 | t_36286d5b01db4126 | failed | seq 54: `plateau` | `verify.py: passed=False score=0.25` (seq 40, 46, 52) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-006/work/garden.ini` |
| config_edit-008 | t_ddc295104da647ad | failed | seq 75: `plateau` | `verify.py: passed=False score=0.0` (seq 61, 67, 73) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-008/work/garden.ini` |
| config_edit-010 | t_b124ae23d9ce427e | failed | seq 62: `plateau` | `verify.py: passed=False score=0.0` (seq 34, 54, 60) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-010/work/garden.ini` |
| config_edit-012 | t_5fbeb94273b3427e | failed | seq 54: `plateau` | `verify.py: passed=False score=0.25` (seq 40, 46, 52) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-012/work/garden.ini` |
| config_edit-014 | t_869f3b3f2d6241eb | failed | seq 54: `plateau` | `verify.py: passed=False score=0.0` (seq 40, 46, 52) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-014/work/garden.ini` |
| config_edit-016 | t_25bb94eb01844115 | failed | seq 54: `plateau` | `verify.py: passed=False score=0.0` (seq 40, 46, 52) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-016/work/garden.ini` |
| config_edit-018 | t_eea531e56b024f9d | failed | seq 69: `plateau` | `verify.py: passed=False score=0.0` (seq 55, 61, 67) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-018/work/garden.ini` |
| config_edit-020 | t_f82f4e1e62f2444f | failed | seq 54: `plateau` | `verify.py: passed=False score=0.25` (seq 40, 46, 52) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-020/work/garden.ini` |
| config_edit-022 | t_72043825c0fa4967 | failed | seq 54: `plateau` | `verify.py: passed=False score=0.0` (seq 40, 46, 52) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-022/work/garden.ini` |
| config_edit-024 | t_e318056db52b49f7 | failed | seq 54: `plateau` | `verify.py: passed=False score=0.0` (seq 40, 46, 52) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-024/work/garden.ini` |
| config_edit-026 | t_bc45cb1413794f6b | failed | seq 69: `plateau` | `verify.py: passed=False score=0.0` (seq 55, 61, 67) | `/home/user/bucephalus/runs/cloud-8b-cfg-3/data/kept/config_edit-026/work/garden.ini` |

- Each instance ended `failed` with reason `plateau` after 3 failed verification attempts: `score=0.0` in 11
  instances, `score=0.25` in 3 (006, 012, 020), each of which had one applied single-line edit.
- Every final answer is `{"path": "<this instance's folder>/work/garden.ini"}`, an absolute path. `base`'s 14 passing
  answers in `cloud-8b-cfg-2` are absolute paths too (read from its archive, whose SHA-256 matched), so the absolute
  answer path does not separate the two arms.

## What is odd

- `kept` is 0/14 again, but the cause moved. The gate refusals are gone (26 to 0) and absolute paths pass. The
  failures are now the model's edits: 14 of 22 calls failed on an anchor that did not match the file, and the 8
  applied edits (in 8 instances) did not make verification pass.
- Anchors: the model often gave a bare line number (`anchor 3`) or a stale hash; the error says to read the file
  again. I did not check whether it did, or what the file's anchors were at that moment.
- The first warm-up failed to load the model (see "Two attempts"); the retry loaded in 145 s.

## Not checked

- Why each anchor did not match: I did not compare the anchors the model used with the file's anchors at that step,
  or check whether it re-read the file after the error.
- What `verify.py` requires, why three instances scored 0.25, and why the 8 applied edits fail verification: the
  grader and the final contents of `work/garden.ini` were not opened.
- Whether `base` would still pass 14/14 on harness 19: it was not re-run, and the copied rows are harness 18.
- Whether harness 19 changed anything else the `kept` arm does; only the events above were read, not the source.
- The 22 `edit_first.held` events and the `model.thought` and `carry.updated` events in the `kept` databases.
- The full test suite: only the three named test files were run.
- The run used Linux and Python 3.12, not the Windows setup the handover describes.

## Raw run data

`cloud-8b-cfg-3-full.tar.gz` (in this folder) was made with `tar -czf` over `runs/cloud-8b-cfg-3` before this
NOTES.md was written, so it holds everything here except NOTES.md: `data/kept/<key>/agent.db` (tables `tasks`,
`events`, `artifacts`, `calls`) for the 14 instances, the agents' work files, both attempts' console and Ollama logs,
plus `base.jsonl`, `kept.jsonl`, `summary.json`, `cards.json` and `cards/`. Size 474,394 bytes, SHA-256
`f96c881012fec7f39645729b1881f519682d67067131bbf4b3f4341da1b70340`. Extract into an empty directory and treat its
contents as untrusted model output.
