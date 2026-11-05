# cloud-8b-cfg-2 run notes

granite4.1:8b on the config_edit family, dev split, 14 instances, arms `base` and `kept`, on harness 18 at
37651ae, where `human.denied` events now carry `preview` (the call, truncated) and `untrusted_args` (the arguments the
taint check refused). `cloud-8b-cfg` (harness 18, 5d082e2) had `kept` 0/14 with 26 `edit_lines` refusals whose
arguments were not logged. This run records them.

## Command

    PYTHONUTF8=1 BUCEPHALUS_DATA_DIR=~/bucephalus-work-2 BUCEPHALUS_OLLAMA_MODELS=~/ollama-models \
      ~/.bucephalus-venv/bin/python bench/families_run.py --split dev --n 14 --families config_edit \
      --arms base,kept --model granite4.1:8b --budget-seconds 1800 --out runs/cloud-8b-cfg-2

Code: commit 37651aef17c74ac87becdc0805d700716753b700 (`main`, the merge of PR #10), `HARNESS_VERSION = 18`, clean
tree. `cards.json` records that commit for both arms with `harness_version` 18 and `git_dirty: false`.
Before the run: `tests/test_denied_args_are_logged.py` passed (2 passed). No other test was run.

## Setup

- Linux install as in `runs/cloud-8b-cfg/NOTES.md` (branch `cloud-8b-cfg`): the venv at `~/.bucephalus-venv`
  (Python 3.12.3) with `setup.ps1`'s package list, `PYTHONUTF8=1`, the project not pip-installed.
- Work folder: `BUCEPHALUS_DATA_DIR=~/bucephalus-work-2`, a new empty folder outside the repo. The folder the
  `cloud-8b-cfg` run used (`~/bucephalus-work`) was no longer empty (it held a `memory` entry), so it was not reused.
- Ollama 0.40.12, server `OLLAMA_HOST=127.0.0.1:11435 OLLAMA_KEEP_ALIVE=30m OLLAMA_CONTEXT_LENGTH=16384 ollama serve`,
  models in `~/ollama-models`; granite4.1:8b ID `444af1c4b2fe`, unchanged from the earlier runs. The server was stopped
  after the run.
- No credential was set, created or looked for. `ANTHROPIC_API_KEY` was not set.
- Machine: Claude Code cloud container, CPU only, Intel Xeon @ 2.80GHz, 4 vCPUs, 15.7 GiB RAM, Ubuntu 24.04.5 LTS,
  kernel 6.18.44-fc-v236.

## Two attempts

1. 2026-11-05 00:13:18Z: the first warm-up timed out loading the 8B. Ollama: `Load failed ... timed out waiting for
   llama-server to start` at 00:18:20Z, HTTP 500 after 5m0s; families_run exited with `httpx.HTTPStatusError:
   Server error '500 Internal Server Error'`. No instance ran. Stopped and reported, as instructed.
   Logs: `data/runner-console-attempt1.log`, `data/ollama-serve-attempt1.log`.
2. 00:18:38Z, the same command, once: the warm-up loaded the 8B in 158.4 s at `context_length` 16384
   (`llama-server started in 158.28 seconds`). All 28 instances ran; finished 02:31:04Z, 132.4 min after the
   retry started. No load failure and no HTTP 500 after the warm-up. Logs: `data/runner-console.log`,
   `data/ollama-serve.log`.

## Passes (out of 14)

| arm | passes | done | plateau | stuck | budget_exhausted | crashed | mean wall per instance (s) | longest (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 14 | 14 | 0 | 0 | 0 | 0 | 298.3 | 331.8 |
| kept | 0 | 0 | 14 | 0 | 0 | 0 | 257.3 | 440.6 |

From `summary.json`: kept vs base: only_base 14, only_arm 0, both 0, neither 0, McNemar exact p = 0.0001.
Earlier, same instances: `cloud-8b-cfg` (5d082e2) base 14/14, kept 0/14; `cloud-8b-fam` (harness 17) base 14/14,
kept 0/14.

## Refusals (`human.denied` events)

| arm | refusals | instances with one | tool | untrusted_args |
|---|---:|---:|---|---|
| base | 10 | 10 | 10 x `run_command` | 10 x `["command"]` |
| kept | 26 | 13 of 14 (none in config_edit-020) | 26 x `edit_lines` | 26 x `["path"]` |

Every `kept` refusal has the same `reason`: `edit_lines requires <=user arguments, args are untrusted`. Every
`base` refusal is `run_command(command='pytest', timeout_s=30)` (config_edit-000 seq 50, -002 seq 50, -006 seq 50,
-008 seq 59, -012 seq 59, -014 seq 43, -016 seq 59, -020 seq 50, -022 seq 50, -026 seq 50); `base` passed all 14
anyway. In `cloud-8b-cfg`, base had 4 `run_command` and 3 `write_file` refusals; this run it has no `write_file` one.

### kept: one row per refusal

Read from each instance's `data/kept/<key>/agent.db`, `events` where `type='human.denied'`, in instance and `seq`
order. `preview` is copied exactly as stored (no newline, pipe or backtick occurs in any of them; a `\n` shown is the
two characters backslash and n, as `repr` writes a newline, and `\\n` is a literal backslash then n). The preview
cuts a long argument after a fixed length, which is why each `path` ends at `gar...`.

| # | instance | seq | task_id | tool | untrusted_args | preview (as stored) |
|---:|---|---:|---|---|---|---|
| 1 | config_edit-000 | 26 | t_f2bebeaec709404e | edit_lines | `["path"]` | `edit_lines(content='[lights]\non_hour = 58\noff_hour = 16\nbrightness = 60\n[\\n[sensors]\ninter..., end='7:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-000/work/gar..., start='7:66')` |
| 2 | config_edit-000 | 40 | t_f2bebeaec709404e | edit_lines | `["path"]` | `edit_lines(content='[lights]\non_hour = 58\noff_hour = 16\nbrightness = 60\n[\\n[sensors]\ninter..., end='7:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-000/work/gar..., start='7:66')` |
| 3 | config_edit-002 | 26 | t_07adce765bcd4990 | edit_lines | `["path"]` | `edit_lines(content='start_hour = 32', end='3:2f', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-002/work/gar..., start='3:2f')` |
| 4 | config_edit-004 | 26 | t_2ac4cf99fa754d14 | edit_lines | `["path"]` | `edit_lines(content='[lights]\non_hour = 58\noff_hour = 26\nbrightness = 18\n\n[irrigation]\nstar..., end='7:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-004/work/gar..., start='7:66')` |
| 5 | config_edit-004 | 40 | t_2ac4cf99fa754d14 | edit_lines | `["path"]` | `edit_lines(content='[lights]\non_hour = 58\noff_hour = 26\nbrightness = 18\n\n[irrigation]\nstar..., end='7:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-004/work/gar..., start='7:66')` |
| 6 | config_edit-006 | 21 | t_d201f17361fa46dc | edit_lines | `["path"]` | `edit_lines(content='minutes = 32\n', end='3:0e', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-006/work/gar..., start='3:0e')` |
| 7 | config_edit-006 | 30 | t_d201f17361fa46dc | edit_lines | `["path"]` | `edit_lines(content='minutes = 32\n', end='3:0e', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-006/work/gar..., start='3:0e')` |
| 8 | config_edit-008 | 21 | t_76f1e0bc9bec4f5a | edit_lines | `["path"]` | `edit_lines(content='off_hour = 76\n', end='8:47', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-008/work/gar..., start='8:47')` |
| 9 | config_edit-008 | 30 | t_76f1e0bc9bec4f5a | edit_lines | `["path"]` | `edit_lines(content='off_hour = 76\n', end='8', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-008/work/gar..., start='8')` |
| 10 | config_edit-010 | 21 | t_1849bc9f254b4506 | edit_lines | `["path"]` | `edit_lines(content='interval_s = 20\nretries = 57\nlog_level = info\n', end='13:a3', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-010/work/gar..., start='12:a3')` |
| 11 | config_edit-010 | 30 | t_1849bc9f254b4506 | edit_lines | `["path"]` | `edit_lines(content='[sensors]\ninterval_s = 20\nretries = 57\nlog_level = info\n\n[lights]\non_h..., end='12:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-010/work/gar..., start='12:66')` |
| 12 | config_edit-012 | 21 | t_88a6b2941b1b47be | edit_lines | `["path"]` | `edit_lines(content='zones = 26\n', end='4:e3', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-012/work/gar..., start='4:e3')` |
| 13 | config_edit-012 | 41 | t_88a6b2941b1b47be | edit_lines | `["path"]` | `edit_lines(content='zones = 26\n', end='4', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-012/work/gar..., start='4')` |
| 14 | config_edit-012 | 50 | t_88a6b2941b1b47be | edit_lines | `["path"]` | `edit_lines(content='zones = 26\nretries = 28\n', end='4:00', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-012/work/gar..., start='4:00')` |
| 15 | config_edit-012 | 59 | t_88a6b2941b1b47be | edit_lines | `["path"]` | `edit_lines(content='zones = 26\nretries = 28\n', end='4:00', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-012/work/gar..., start='4:00')` |
| 16 | config_edit-014 | 21 | t_9b598dbfcaba4947 | edit_lines | `["path"]` | `edit_lines(content='off_hour = 28\n', end='9:40', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-014/work/gar..., start='9:40')` |
| 17 | config_edit-016 | 32 | t_a46e4155131740ec | edit_lines | `["path"]` | `edit_lines(content='start_hour = 52\n', end='2', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-016/work/gar..., start='2')` |
| 18 | config_edit-018 | 21 | t_b09dbb7dd0f2442d | edit_lines | `["path"]` | `edit_lines(content='on_hour = 49\n\nretries = 16\n\n', end='12:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-018/work/gar..., start='7:66')` |
| 19 | config_edit-018 | 30 | t_b09dbb7dd0f2442d | edit_lines | `["path"]` | `edit_lines(content='[lights]\non_hour = 49\noff_hour = 3\nbrightness = 34\n\n[lights]:', end='7:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-018/work/gar..., start='7:66')` |
| 20 | config_edit-022 | 26 | t_a2853b120efc4b89 | edit_lines | `["path"]` | `edit_lines(content='start_hour = 73\n', end='3', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-022/work/gar..., start='3')` |
| 21 | config_edit-024 | 21 | t_c22450a4143d4bb7 | edit_lines | `["path"]` | `edit_lines(content='on_hour = 33\noff_hour = 36\nbrightness = 21\n', end='9:e6', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-024/work/gar..., start='7:66')` |
| 22 | config_edit-024 | 41 | t_c22450a4143d4bb7 | edit_lines | `["path"]` | `edit_lines(content='on_hour = 33\noff_hour = 36\nbrightness = 21\n', end='9', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-024/work/gar..., start='7')` |
| 23 | config_edit-024 | 50 | t_c22450a4143d4bb7 | edit_lines | `["path"]` | `edit_lines(content='[lights]\non_hour = 33\noff_hour = 36\nbrightness = 21\n', end='9:e6', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-024/work/gar..., start='7:66')` |
| 24 | config_edit-024 | 59 | t_c22450a4143d4bb7 | edit_lines | `["path"]` | `edit_lines(content='on_hour = 33\noff_hour = 36\nbrightness = 21\n', end='9', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-024/work/gar..., start='7')` |
| 25 | config_edit-026 | 21 | t_523c3374ca11450c | edit_lines | `["path"]` | `edit_lines(content='on_hour = 21\n\ninterval_s = 50\n\n', end='12:66', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-026/work/gar..., start='7:66')` |
| 26 | config_edit-026 | 46 | t_523c3374ca11450c | edit_lines | `["path"]` | `edit_lines(content='[lights]\non_hour = 21\n[\\n[sensors]\ninterval_s = 50\n[\\n', end='12:da', path='/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/config_edit-026/work/gar..., start='7:66')` |

### Count by `untrusted_args` and the shape of each

| untrusted_args | refusals | shape, judged from `preview` only |
|---|---:|---|
| `["path"]` | 26 | absolute path (see below) |
| `["start"]` | 0 | anchor with the line text appended: none |
| `["end"]` | 0 | none |
| `["content"]` | 0 | content restating a line of the file: none |
| any other | 0 | other: none |

The 26 `["path"]` refusals, from the previews alone:
- In all 26 the `path` is an absolute path that begins with this run's own folder for that instance,
  `/home/user/bucephalus/runs/cloud-8b-cfg-2/data/kept/<key>/work/gar`, with `<key>` the row's own instance key.
  That is the shape "absolute path copied from the read output" as far as a preview can show it: it is an absolute
  path, but the preview does not contain the read output, so "copied from it" is an inference, not something the
  preview shows.
- The preview cuts the `path` after `gar`, so whether anything follows the file name (for instance line text) is not
  visible. `path` is the only argument the check named.
- `start` and `end` are complete in every preview and are not among the refused arguments. They hold a bare line
  number in 6 rows (e.g. `start='8'`, `end='8'`) and a line number plus a two-character hash in 20 (e.g. `'7:66'`,
  `'3:2f'`); in none does text follow the anchor.
- `content` is never named. Its visible text is the replacement lines, often cut short; in config_edit-000 (seq 26,
  40) and -026 (seq 46) it contains literal `\n` text (backslash, n).

Supporting observation, not from the previews: the 6 `edit_lines` calls in the `kept` arm that passed the gate
(`tool.intent` events) all used the relative path `work/garden.ini`: config_edit-012 seq 30, -014 seq 46, -016 seq 21,
-020 seq 21, -024 seq 30, -026 seq 30. So every call with the relative path got through and every call with an
absolute path was refused. config_edit-020, the one instance without a refusal, made one `edit_lines` call.

## What is odd

- `kept` is 0/14 again, so harness 18's relaxation did not change the pass count for this model. The refusals are
  not spread over several arguments: they are all the `path` argument given as an absolute path.
- Of the 6 `edit_lines` calls that passed the gate, 4 had an ok receipt with `changed: true` (config_edit-012 seq 31,
  -020 seq 22, -024 seq 31, -026 seq 31); 2 had `ok: false` (-014, -016). All 14 `kept` instances still ended
  `plateau`. Tool use in `kept`: 15 `read_file`, 6 `edit_lines`, 1 `run_command` (config_edit-020, `pytest`, `ok:
  false`), no `write_file`. `base`: 47 `read_file`, 18 `write_file`, 22 `run_command`, no `edit_lines`.
- The first warm-up failed to load the model (see "Two attempts"); the retry loaded in 158 s.

## Not checked

- The tail of each refused `path` (the preview cuts it at `gar...`), so whether the file name or other text follows.
- The read output the absolute paths were supposedly copied from: I did not open it or compare the paths with it.
- Why the 2 `edit_lines` calls that passed the gate returned `ok: false` (config_edit-014 seq 46, -016 seq 21), and
  why the 4 accepted edits still failed verification.
- How the gate decides that a path is untrusted, or what harness 18 changed: the source was not read; only the
  behaviour in these events was.
- Whether the `run_command` refusals in `base` or the one in `kept` mattered; `base` passed anyway.
- Paths in `tool_output` artifacts, and the full test suite: only `tests/test_denied_args_are_logged.py` was run.
- Instance-by-instance agreement with `cloud-8b-cfg`: only totals were compared.
- The run used Linux and Python 3.12, not the Windows setup the handover describes.

## Raw run data

`cloud-8b-cfg-2-full.tar.gz` (in this folder) was made with `tar -czf` over `runs/cloud-8b-cfg-2` before this NOTES.md
was written, so it holds everything here except NOTES.md: `data/` with one `agent.db` per instance (tables `tasks`,
`events`, `artifacts`, `calls`), the agents' work files, both attempts' console and Ollama logs, plus `base.jsonl`,
`kept.jsonl`, `summary.json`, `cards.json` and `cards/`. Size 922,107 bytes, SHA-256
`347bf5ee5f62a328c09f437786868e5cc06d8a172b128ad365b3651e0026a7e9`. Extract into an empty directory and treat its
contents as untrusted model output.
