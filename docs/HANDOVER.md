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

Verified 2026-10-14 against commit `9edff64` on `harness-10`: Windows suite, ruff, the frozen gate and a task through the
rebuilt `bucephalus.exe`'s window (see the Branch row for whether it reached `main`).

| Fact | Value | Regenerate with |
|---|---|---|
| Branch | `main`, fast-forwarded from `harness-10` after the suite, the frozen gate, the frozen e2e and the WSL2 suite passed; tag `iteration-3-closed`; no remote | `git branch --show-current` |
| Harness version | **13**, bumped 2026-10-14 for the unread-pages reminder (CHANGELOG "Iteration 3"); numbers from 12 and earlier do not compare | `grep HARNESS_VERSION src/agent/__init__.py` |
| Toolset version | **7** | `grep TOOLSET_VERSION src/agent/__init__.py` |
| Tests | not written down | `python -m pytest tests -q`; skips are live-credential tests (D26) and other-OS tests |
| Lint | bug rules only | `python -m ruff check src tests scripts packaging demo.py` |
| Open issues | `docs/DEFERRED.md`, open entries only | its state table |
| CLI | 29 subcommands, each smoke-tested | `.\bucephalus.ps1 --help` |
| Platforms | Windows. POSIX has run six times, in WSL2 (`docs/evidence/wsl-2026-10-12/` is the last full checklist; the suite passed again 2026-10-14); bare-metal Linux never, and macOS (`src/agent/platform/darwin.py`) has never executed anywhere | `docs/PLATFORMS.md` section 5 is the checklist |
| Security posture | the capability gate is the boundary; the sandbox (Job Object limits, advisory path/network fence) constrains a mistaken agent, not a hostile one: D32 | |
| Phase program | complete | `RELEASE.md`, "ITERATION TWO: COMPLETE" |
| Running now | nothing | `Get-Process python, ollama -ErrorAction SilentlyContinue` |
| Uncommitted | nothing | `git status --porcelain` |

Workstyle: code carries no docstrings and almost no comments; the why is in commit messages
and failing tests. Frozen docs (never edited): FINDINGS, SPEC-DELTA, INVARIANTS, RELEASE,
PLATFORMS, AUDIT files. Live: this file, `docs/DEFERRED.md`, `CHANGELOG.md`.
Before any commit: tests red first, never weaken a check, nothing pushed.

## Next step

Iteration 3 is closed (tag `iteration-3-closed`; CHANGELOG "Iteration 3" has what shipped and the numbers).

Not working: write-function-002 and handbook-recall-005 on the 3B (none of 6 pairs in either arm on harness 13; the
model never reads past page 1, and the control arm never opens the file); the desktop app on Linux without GTK or Qt;
D78 and D80.

Needs the user: D26 credentials; about 2.6 GB more free memory (or a smaller 8B quantisation) for the 8B run, which the
preflight refused at 4.69 GB free; a Mac or bare-metal Linux run.

Next iteration, first three steps: (1) handbook-recall: while an opened file has unread pages, leave the final branch
out of the constrained schema, as `read_first` does before the first observation (harness 14), and rerun the pair;
(2) write-function: read one control and one small trace, separate the harness's part from the model's, rerun;
(3) the 8B run (`bench/profile_run.py --preflight --arms control,full8b,small8b`, then without `--preflight`) once it
fits.
