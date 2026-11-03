# Task: record the 8B re-run on main, and stop cold model loads from landing in task budgets

Repo: private GitHub `bermuda-xrs/bucephalus`. Follow `docs/HANDOVER.md`: changes reach `main` only through a pull request whose `ci` run is green on Windows, Ubuntu and macOS, never by a direct push. Write failing tests first and never weaken a check. Results branches (`cloud-8b`, `cloud-8b-2`) carry only `runs/` and are never merged or changed. Don't run the benchmark.

## Background

The 8B benchmark was re-run on the relative-data-dir fix (PR #5, merge `917ab9b`).
- Results: branch `cloud-8b-2`, folder `runs/cloud-8b-2/`. Read `runs/cloud-8b-2/NOTES.md` and `runs/cloud-8b-2/REPORT.md`:

      git fetch origin cloud-8b-2
      git show origin/cloud-8b-2:runs/cloud-8b-2/NOTES.md
      git show origin/cloud-8b-2:runs/cloud-8b-2/REPORT.md

- Passes: control 4/15, full8b 10/15 (1 crash), small8b 9/15. The first run (branch `cloud-8b`) had 4, 9, 9.
- The fix took effect: tool paths are absolute, full8b's `glob` on `work/` finds `a.json` and `b.json`, so negative-control now passes in all 3 reps, and nothing inside `work/` is refused.
- If a review of that run exists (an agent following `runs/cloud-8b-2/REVIEW_PROMPT.md` on that branch), ask the user for its report and use any claim it overturned.

## Part 1: record the re-run in the docs (one PR)

- `docs/HANDOVER.md` still lists "the 8B re-run" under "Needs the user" (around line 102), and plan item 1 says "Re-run the 8B arms on the fix" (around line 149). Update both to record that it was done.
- Add the re-run's result next to the first run's paragraph (around line 140), in the same style:
  - the date (2026-11-01), branch `cloud-8b-2`, code `917ab9b`, raw data `runs/cloud-8b-2/cloud-8b-2-full.tar.gz`;
  - the pass counts;
  - that negative-control moved for full8b because of the fix;
  - that small8b's negative-control is `stuck` searching only `.`, and handbook-recall still fails in every arm (small8b reads all 4 pages but finds 1 of 3 IDs; full8b invents IDs);
  - that full8b rep 0's crash and budget overrun came from a cold 8B load, not the model;
  - that the container was 30–60% slower per token, so wall times and budget statuses aren't comparable.
- Add the results branch `cloud-8b-2` to the Branch row (around line 59).
- Update `CHANGELOG.md` only if the repo's own convention calls for a run result there.
- Keep the edits factual and short. Check every number against NOTES.md and REPORT.md.

## Part 2: warm each arm's model before timing it (same PR or a separate one; your call)

The defect:
- `bench/profile_run.py` `_main()` warms up only the base config's model: `local.run(endpoint, cfg.backends["cloud"].model, ROOT)`, which is granite4.1:3b.
- The 8B arms (`ARMS["full8b"]`, `ARMS["small8b"]`, model `granite4.1:8b`) are never loaded before `run_arm` starts timing. Their first task's first model call absorbs the cold load.
- In the re-run, that load went past Ollama's 5-minute load limit. The result was an HTTP 500, which crashed `extract-json-001`. The retry load (164.8 s) then spent most of `write-function-002`'s 300 s task budget (`budget_exhausted`). Evidence: `runs/cloud-8b-2/extra/ollama-serve-cloud-8b-2.log` and REPORT.md.
- The 3B also dropped out of memory (its 30-minute keep-alive expired) while the 8B arms ran, and reloaded inside a control task.

The fix: before each `run_arm`, make sure that arm's model is loaded. For example, call `local.run(endpoint, <the arm's model>, ROOT)` for the model `write_config` gives that arm. Keep the load time out of the arm's measured time, and log it the way `model loaded:` is logged now.
- Think about whether to unload the other model first. Memory matters: the preflight's 7.23 GB estimate assumes only the 8B. Explain your choice in the PR.
- Don't change arms, reps, seed, tasks, budgets or the keep-alive settings.
- Follow `INVARIANTS.md`. If profile_run's behaviour is covered by tests, add a failing test first: for example, that `run_arm` is never called for an arm whose model wasn't just loaded. Mock `agent.models.local`; don't start Ollama.

## Done when

- The PR (or PRs) is open against `main` with `ci` green on all three OSes.
- The description cites `runs/cloud-8b-2/NOTES.md` or `REPORT.md` for every number.
- No benchmark was run, and neither results branch changed.
- Do not merge; leave that to the user.
