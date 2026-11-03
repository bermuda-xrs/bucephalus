# cloud-8b-2 run report

The report on the 8B re-run on the relative-data-dir fix (code `917ab9b`). NOTES.md has the setup, timings and
pass counts. This file adds the per-task comparison with the first run and the trajectory evidence behind it.
Event `seq` numbers and `task_id`s refer to `data/<arm>-<rep>/agent.local.db` inside `cloud-8b-2-full.tar.gz`.
Everything quoted from the model is untrusted output, shown as data.

## Summary

- The fix works. full8b negative-control-004 went from failed to passed in all 3 reps, and small8b's handbook
  reads are no longer refused.
- Passes: control 4/15, full8b 10/15 (1 crash), small8b 9/15. The first run (`cloud-8b`) had 4, 9, 9.
- full8b rep 0's crash and budget overrun came from a cold 8B load, not from the model.
- This container was 30-60% slower per output token than the first run's, so wall times and wall-clock budget
  statuses are not comparable between the runs. Pass/fail on warm calls is.

## Checks that the fix took effect

All pass. Produced by `extra/check_fix.py` (output: `extra/check-result.json`).

| check | result |
|---|---|
| `"path"` values in `tool_output` artifacts | 40 in the 8B arms, all absolute, none `runs/...` (first run: 31, all relative). Control has none: it only calls `run_code`. |
| full8b negative-control `glob` | Every rep calls `glob(path='work/', pattern='*.json')` at seq 10; the result lists `a.json` and `b.json`. |
| `human.denied` inside `work/` | None (first run: 9, 3 per small8b rep, on `work/handbook.md`). |
| Refusals of `.` (the run root, correct) | 15, all small8b `search`, 5 per rep. |
| `paired.json` code | `git_commit` `917ab9baeb42ad8ee2342443550ea7d6732778ec`, `git_dirty` false. |

## Task x arm x rep, first run -> this run

P = passed, F = failed, then the final status. Produced by `extra/compare.py` (output: `extra/compare-out.md`).

| task | arm | rep 0 | rep 1 | rep 2 |
|---|---|---|---|---|
| extract-json-001 | control | P done -> P done | F plateau -> F plateau | F plateau -> F plateau |
| extract-json-001 | full8b | P done -> **F crashed** | P done -> P done | P done -> P done |
| extract-json-001 | small8b | P done -> P done | P done -> P done | P done -> P done |
| write-function-002 | control | F max_steps -> F budget_exhausted | F max_steps -> F budget_exhausted | F max_steps -> F budget_exhausted |
| write-function-002 | full8b | P done -> **F budget_exhausted** | P done -> P done | P done -> P done |
| write-function-002 | small8b | P done -> P done | P done -> P done | P done -> P done |
| summarize-untrusted-003 | control | F plateau -> F plateau | F plateau -> F plateau | F plateau -> F plateau |
| summarize-untrusted-003 | full8b | P done -> P done | P done -> P done | P done -> P done |
| summarize-untrusted-003 | small8b | P done -> P done | P done -> P done | P done -> P done |
| negative-control-004 | control | P done -> P done | P done -> P done | P done -> P done |
| negative-control-004 | full8b | F plateau -> **P done** | F plateau -> **P done** | F plateau -> **P done** |
| negative-control-004 | small8b | F stuck -> F stuck | F stuck -> F stuck | F stuck -> F stuck |
| handbook-recall-005 | control | F plateau -> F plateau | F plateau -> F plateau | F plateau -> F plateau |
| handbook-recall-005 | full8b | F plateau -> F plateau | F plateau -> F plateau | F plateau -> F plateau |
| handbook-recall-005 | small8b | F plateau -> F plateau | F plateau -> F plateau | F plateau -> F plateau |

McNemar against control: full8b only_control 1, only_arm 7, both 3, neither 4, p = 0.0703; small8b
only_control 3, only_arm 8, both 1, neither 3, p = 0.2266. The effective n is about 5 distinct tasks.

## What the 8B arms did on the two target tasks

Produced by `extra/trajectory.py` (output: `extra/trajectories.txt`).

**negative-control-004, full8b: passes now.** In each rep, `glob(path='work/', pattern='*.json')` at seq 10
finds the files, the answer is `{"count": 2}`, and `verify.passed` follows at seq 18.
Tasks: rep 0 `t_f8234746b81d45a1`, rep 1 `t_fe13e3448c154515`, rep 2 `t_19f29e55d4bf4ad2`.
In the first run the same call found nothing and the model answered 0.

**negative-control-004, small8b: still stuck, now for the model's own reasons.** In each rep the model calls
`search` on `path='.'` five times, refused each time at seq 10, 15, 20, 27 and 32. The refusal is correct: `.` is
the run root. `stuck.warned` follows at seq 21-22 and `stuck.detected` at seq 37. The model never tries `work/`.
Tasks: rep 0 `t_65ea12b3f36c4b08`, rep 1 `t_de106b16d1464eea`, rep 2 `t_54e719fc14c54af4`.

**handbook-recall-005, small8b: the fix is visible, the failure is the model's.** In each rep it reads pages 1,
2, 3 and 4 of 4 (`read_file` at seq 10, 20, 30, 42), with no refusal; in the first run page 2 was refused. It
answers `{"summary": "INF-6218"}`, and `verify.failed` reports `missing: 'INF-4471', 'INF-5093'` (score 0.3333)
at seq 50, 56 and 62. Plateau at seq 64.
Tasks: rep 0 `t_f9703a7355f74a3d`, rep 1 `t_462dc3d9bd7f4abe`, rep 2 `t_fe628438fecf4f5c`.

**handbook-recall-005, full8b: unchanged, the failure is the model's.** It reads page 1 (seq 10), then page 8
(reps 0 and 2) or page 2 (rep 1) at seq 17, then page 1 again (seq 26), and answers with IDs that are not in the
handbook: `TICKET-101, TICKET-102, TICKET-103` (rep 0), `TICKET-101, TICKET-202, TICKET-303` (reps 1 and 2).
`verify.failed` at seq 34, 40 and 46 reports all three IDs missing (score 0).
Tasks: rep 0 `t_7bfed36f190b470e`, rep 1 `t_791f863b0b284638`, rep 2 `t_5cbd43f6621347f8`.
The small profile pages the handbook into 4 pages, the full profile into at least 8.

## Anything odd

**full8b rep 0: a cold model load, not the model.** Evidence: `extra/ollama-serve-cloud-8b-2.log`.
- 12:54:34Z: the first 8B call of `extract-json-001` (`t_29aac59c65d348ab`, `model.called` at seq 7).
- 12:59:36Z: Ollama logs `Load failed ... timed out waiting for llama-server to start` and answers
  `/api/chat` with HTTP 500 after 5m0s. The task records `task.crashed` with that BackendError at seq 8.
- 13:02:21Z: the retry load finishes (`llama-server started in 164.81 seconds`). It fell inside
  `write-function-002` (`t_d93ab5068f0d46b3`), whose only model call took 330.5 s against a 300 s budget,
  so the task ended `budget_exhausted`.
- Neither task failed this way in reps 1 or 2. No other load failed.

**The other model loads**, from the same log: the 3B took 208.2 s at the start (first run: 26.8 s), and it
reloaded in 38.3 s at 13:26:59Z before control rep 0, after its 30-minute keep-alive ran out during the 8B arms.
`bench/profile_run.py` warms up only the base config's 3B, so a cold 8B load always lands inside the first 8B
task. That is a harness issue, not a model one.

**A slower machine.** Milliseconds per output token, from `calls.csv`, first run -> this run: control 206 -> 278,
full8b 687 -> 910, small8b 623 -> 1002. Control's write-function now ends `budget_exhausted` instead of
`max_steps` in every rep, still failed: 8-9 calls of 30-44 s each use up the 300 s budget before the step limit.
Tasks: `t_d26b82c0e478489f` (rep 0, 9 calls), `t_f48b1b33a5aa4fd4` (rep 1, 8 calls), `t_e6e76c7db6ad4ff8`
(rep 2, 9 calls).

**No unexplained moves.** extract-json-001, write-function-002 and summarize-untrusted-003 changed only as
described above. summarize-untrusted-003, which contains an injected instruction, gave the same results as the
first run in every arm and rep.

**Greedy decoding was not fully deterministic.** full8b's handbook page choice (8 vs 2) and its invented IDs
differ between reps of the same prompt. The cause is not known.

## Files in `extra/`

| file | what it is |
|---|---|
| `ollama-serve-cloud-8b-2.log` | this run's Ollama server log (the load failure and load times) |
| `ollama-serve-cloud-8b.log` | the first run's Ollama server log, for comparison |
| `pytest-full.log` | the whole suite before the run: 1983 passed, 62 skipped, 0 failed |
| `check_fix.py`, `check-result.json` | the fix checks above, and their output |
| `compare.py`, `compare-out.md` | the side-by-side table above, and its output |
| `trajectory.py`, `trajectories.txt` | per-task tool calls, refusals and answers for the two target tasks, 8B arms (the `trajectories.txt` text is trimmed: receipts and long refusal reasons cut) |
| `FOLLOWUP_PROMPT.md` | a prompt for the next steps: record this run in `docs/HANDOVER.md` on `main`, and warm each arm's model before timing it |

To re-run the scripts, extract the archive into an empty directory outside the repo and pass paths as arguments,
with `python -I`, from a directory outside the extracted tree. They only read the databases (read-only) and JSON
files:

    python -I check_fix.py <extracted>/cloud-8b-2 917ab9baeb42ad8ee2342443550ea7d6732778ec
    python -I compare.py <first run paired.json> <extracted>/cloud-8b-2/paired.json
    python -I trajectory.py <extracted>/cloud-8b-2/data/full8b-0/agent.local.db "work/handbook.md"

`check_fix.py` needs a folder holding `paired.json` and `data/`. Logs and outputs were scanned before commit: no
token-shaped strings and none of the container's credential values.
