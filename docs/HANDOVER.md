# Handover

State and next step. This directory plus this file is enough to continue; the why of every
change is in its commit message. `tests/test_handover_is_current.py` fails when this file drifts.

## What this is

An agent runtime on local models through Ollama: you give an objective, sign a plan, it
works unattended and reports back (two touches). Hash-chained event log, a capability gate
on tool arguments by taint, a sandboxed interpreter for model code, a console (`bucephalus serve`),
a TUI (`bucephalus tui`) and a desktop app (`src/agent/desktop.py`). The original research
question (do self-generated skills help) was retired unanswered: FINDINGS 1.27.

## Running it

Windows; Python 3.11 or 3.12; Ollama 0.32.7 or later. Traps:
- `ANTHROPIC_API_KEY` must be unset: `--profile local` refuses to start with any credential visible.
- This path has Greek characters: `pip install -e .` and `.cmd` launchers fail. Use the `.ps1`
  scripts and `PYTHONUTF8=1`. Wheel builds: `pip wheel . --no-deps -w dist`.
- Only the `bucephalus` under `OneDrive\Desktop` is live.
- The checkout was renamed from `alpha-test-1` to `bucephalus` on 2026-10-13. To rename it again, run
  `scripts/rename_checkout.ps1` from outside it: it renames the folder
  and moves its per-checkout state directory (`<data dir>\checkouts\<folder name>-<hash of its path>`)
  to match. `-WhatIf` shows what it would do.
- The venv lives at `%USERPROFILE%\.bucephalus-venv` (OneDrive deleted an in-folder one twice).

```powershell
.\setup.ps1
ollama pull granite4.1:3b
.\ollama-serve.ps1
$env:PYTHONUTF8 = "1"
& "$env:USERPROFILE\.bucephalus-venv\Scripts\python.exe" demo.py
```

`.\ollama-serve.ps1` is the project's own server on 11435; the user's Ollama on 11434 is never touched.
`demo.py` must end with the gate refusing `append_to_outbox` and `chain verifies: True`.

## B3. What each command costs

| Command | Cost |
|---|---|
| `demo.py` | ~30 s, scripted model, no network |
| `python -m pytest tests -q` | ~1.5 min in parallel, offline; `-n 0` serial ~5 min |
| `packaging/build.py` | ~4 min, the desktop app (62 MB); `--probe` builds the frozen sandbox gate |
| `bench/report.py`, `bench/inventory.py` | instant, read-only |
| `bench/noise.py --runs 1` | ~2.5 h, takes the machine; resumable with its `order_seed` |
| `bench/run.py paired` | ~4 h |
| `bench/profile_run.py --suite bench/tasks` | ~35 min, 3B, control vs small profile; keep the machine awake (Modern Standby stalls it) |

One measurement process at a time (`src/agent/runlock.py`); nothing else runs during a timed run.

## State

Verified 2026-10-26 against commit `0c7a38e` on `heldout-h16`: Windows suite, ruff, the frozen gate and a task through the
rebuilt `bucephalus.exe`'s window, and the WSL2 suite (see the Branch row for whether it reached `main`).

| Fact | Value | Regenerate with |
|---|---|---|
| Branch | `main`, fast-forwarded from `harness-14` (iteration 4, two whole-source reviews, the harness-16 baseline) after the suite, the frozen gate, the frozen e2e and the WSL2 suite passed; tags `iteration-4-closed`, `review-2-closed`; no remote | `git branch --show-current` |
| Harness version | **16**, bumped 2026-10-25 for the second whole-code review (CHANGELOG "Harness 16"); numbers from 15 and earlier do not compare | `grep HARNESS_VERSION src/agent/__init__.py` |
| Toolset version | **7** | `grep TOOLSET_VERSION src/agent/__init__.py` |
| Tests | not written down | `python -m pytest tests -q`; skips are live-credential tests (D26) and other-OS tests |
| Lint | bug rules only | `python -m ruff check src tests scripts packaging demo.py` |
| Open issues | `docs/DEFERRED.md`, open entries only | its state table |
| CLI | 30 subcommands, each smoke-tested | `.\bucephalus.ps1 --help` |
| Platforms | Windows. POSIX has run six times, in WSL2 (`docs/evidence/wsl-2026-10-12/` is the last full checklist; the suite passed again 2026-10-25); bare-metal Linux never, and macOS (`src/agent/platform/darwin.py`) has never executed anywhere | `docs/PLATFORMS.md` section 5 is the checklist |
| Security posture | the capability gate is the boundary; the sandbox (Job Object limits, advisory path/network fence) constrains a mistaken agent, not a hostile one: D32 | |
| Phase program | complete | `RELEASE.md`, "ITERATION TWO: COMPLETE" |
| Running now | nothing | `Get-Process python, ollama -ErrorAction SilentlyContinue` |
| Uncommitted | nothing | `git status --porcelain` |

Workstyle: code carries no docstrings and almost no comments; the why is in commit messages
and failing tests. Frozen docs (never edited): FINDINGS, SPEC-DELTA, INVARIANTS, RELEASE,
PLATFORMS, AUDIT files. Live: this file, `docs/DEFERRED.md`, `CHANGELOG.md`.
Before any commit: tests red first, never weaken a check, nothing pushed.

## Next step

Iteration 4 (harness 15, CHANGELOG "Harness 15") measured every item on held-out generated families
(`runs/iter4-heldout-20261015`, 3B, n = 12 paired instances per item over 2 families, n = 48 for the base). On for the
small profile: C1 evidence pins and C3 ceiling 0.5 (multipage 0/6 -> 6/6 and 5/6), B2 carry and B6 compact prompt
(same passes, fewer tokens), stuck detection, the escape diagnostic, E1 hashline edits. Off: B1 (was on), B3, B4
(11 -> 3), B5, C2, the evaluator pass, the tool list. F (isolation) was not built.

Harness-16 baseline (`runs/h16-heldout-20261025`, 3B, held-out seeds 13-39 that no arm had seen, n = 112 paired
instances over 8 families, 14 each): base 54/112, all kept items together 66/112 (18 only kept, 6 only base, McNemar
exact p = 0.023), with a smaller prompt in every family. They add up on multipage (0/14 -> 11/14) and write_function
(10/14 -> 14/14), but multi_file falls 11/14 -> 7/14; only D2 had been measured on multi_file alone. Same on needle and
extract_json (14/14), unanswerable (5/14), config_edit (0/14); arithmetic 0/14 -> 1/14.

Read (2026-10-26, no code): config_edit fails because the model never edits. All 58 failed runs, every arm, call
only read_file, then answer with the path; the held-out grader says only "failed", so it re-answers to a plateau.
arithmetic fails on mental arithmetic: 21 failed runs never call run_command; median error 61%.

Not working: multi_file with the kept items together; config_edit and arithmetic on the 3B; the desktop app on Linux
without GTK or Qt; D78 and D80.

Needs the user: compute for the 8B run (needs 7.23 GB; 4.69 GB free here); D26 credentials; a Mac or bare-metal Linux;
a yes or no on plan items 5 and 6.

Plan, in order:
1. multi_file: read the kept arm's 7 failures against base's passes, then one held-out run of each kept item alone on
   multi_file to find which one costs it.
2. config_edit: hold a final answer on a verify_program task while no file has changed (one mechanism, one measured run).
3. arithmetic: point a numeric-total task at run_command (one mechanism, one measured run).
4. The 8B run when compute arrives (`bench/profile_run.py --preflight --arms control,full8b,small8b`).
5. On the user's yes: delete the branches already in `main` (`desktop-app`, `harness-8`, `harness-9`, `harness-10`,
   `harness-14`, `iteration-3`, `iteration-4`, `iteration-5`, `measurement-2026-09-01`, `heldout-h16`); the tags stay.
6. The user's choice: a remote for an off-machine copy (there is none).
