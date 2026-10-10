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
- After renaming the checkout folder, its per-checkout state directory changes too
  (`<data dir>\checkouts\<folder name>-<hash of its path>`), so it starts empty. Move the old one:
  `Move-Item <data dir>\checkouts\<old name>-<old hash> <data dir>\checkouts\<new name>-<new hash>`
  (run `bucephalus status` once to see the new name).
- The old venvs and `~/.alpha-build` were left in place; delete them when ready.
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
| `python -m pytest tests -q` | ~1 min in parallel, offline; `-n 0` serial ~4 min |
| `packaging/build.py` | ~4 min, the desktop app (62 MB); `--probe` builds the frozen sandbox gate |
| `bench/report.py`, `bench/inventory.py` | instant, read-only |
| `bench/noise.py --runs 1` | ~2.5 h, takes the machine; resumable with its `order_seed` |
| `bench/run.py paired` | ~4 h |

One measurement process at a time (`src/agent/runlock.py`); nothing else runs during a timed run.

## State

Verified 2026-10-10 against commit `008e6ba` (the rename): Windows suite, ruff, the frozen gate (63) and a task through the
rebuilt `bucephalus.exe`'s window, all from the renamed tree.

| Fact | Value | Regenerate with |
|---|---|---|
| Branch | `main`, fast-forwarded to `iteration-4` once the suite, the frozen gate and a visual check passed; no remote | `git branch --show-current` |
| Harness version | **7**, bumped 2026-10-05 for P3, P5, P6 (`docs/PROPOSALS.md`); numbers from 6 and earlier do not compare | `grep HARNESS_VERSION src/agent/__init__.py` |
| Toolset version | **6** | `grep TOOLSET_VERSION src/agent/__init__.py` |
| Tests | not written down | `python -m pytest tests -q`; skips are live-credential tests (D26) and other-OS tests |
| Lint | bug rules only | `python -m ruff check src tests scripts packaging demo.py` |
| Open issues | `docs/DEFERRED.md`, open entries only | its state table |
| CLI | 25 subcommands, each smoke-tested | `.\bucephalus.ps1 --help` |
| Platforms | Windows. POSIX has run twice, in WSL2 (`docs/evidence/wsl-2026-09-30/`); bare-metal Linux never, and macOS (`src/agent/platform/darwin.py`) has never executed anywhere | `docs/PLATFORMS.md` section 5 is the checklist |
| Security posture | the capability gate is the boundary; the sandbox (Job Object limits, advisory path/network fence) constrains a mistaken agent, not a hostile one: D32 | |
| Phase program | complete | `RELEASE.md`, "ITERATION TWO: COMPLETE" |
| Running now | nothing | `Get-Process python, ollama -ErrorAction SilentlyContinue` |
| Uncommitted | nothing | `git status --porcelain` |

Workstyle: code carries no docstrings and almost no comments; the why is in commit messages
and failing tests. Frozen docs (never edited): FINDINGS, SPEC-DELTA, INVARIANTS, RELEASE,
PLATFORMS, AUDIT files. Live: this file, `docs/DEFERRED.md`, `CHANGELOG.md`.
Before any commit: tests red first, never weaken a check, nothing pushed.

## Next step

Done: the rename. In progress (user's 2026-10-10 brief): HARNESS_VERSION 8 (small-model
tool profile, post-edit diagnostics, verify-before-stop, constrained tool calls, stuck detection,
shadow-git rewind, AGENTS.md, `exec` mode, hooks, MCP client). Not started: per-tool expandable
cards (needs the stream events to carry full arguments); a real-model re-baseline of HARNESS_VERSION 7.

Waiting on the user: D26 credentials; the memory line a test wrote into the real memory
(`.\bucephalus.ps1 memory --forget "injected instruction"`); a Mac or bare-metal Linux run.
