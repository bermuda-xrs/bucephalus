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
| `bench/profile_run.py --suite bench/tasks` | ~35 min, 3B, control vs small profile |

One measurement process at a time (`src/agent/runlock.py`); nothing else runs during a timed run.

## State

Verified 2026-10-11 against commit `b30ffce` on `harness-8`: Windows suite, ruff, the frozen gate and a task through the rebuilt `bucephalus.exe`'s
window (see the Branch row for whether it reached `main`).

| Fact | Value | Regenerate with |
|---|---|---|
| Branch | `harness-8` (see Next step); `main` is the rename; no remote | `git branch --show-current` |
| Harness version | **8**, bumped 2026-10-11 for the rename and items a-j (CHANGELOG "Harness 8"); numbers from 7 and earlier do not compare | `grep HARNESS_VERSION src/agent/__init__.py` |
| Toolset version | **7** | `grep TOOLSET_VERSION src/agent/__init__.py` |
| Tests | not written down | `python -m pytest tests -q`; skips are live-credential tests (D26) and other-OS tests |
| Lint | bug rules only | `python -m ruff check src tests scripts packaging demo.py` |
| Open issues | `docs/DEFERRED.md`, open entries only | its state table |
| CLI | 27 subcommands, each smoke-tested | `.\bucephalus.ps1 --help` |
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

Done on branch `harness-8` (merged to `main` only if the line under "Branch" above says so): items a-j of the harness
brief, one HARNESS_VERSION 8 / TOOLSET_VERSION 7 bump, profiles per backend (default `full`; `patch`, `small` opt in),
checkpoints and `bucephalus rewind`, AGENTS.md, `bucephalus exec`, hooks, MCP client. CHANGELOG "Harness 8" lists them.

Measured once (`bench/profile_run.py`, granite4.1:3b, `bench/tasks`, 5 tasks x 3 repetitions = 15 pairs, order shuffled
per repetition, 2026-10-11, `runs/profile-small-20261011/paired.json`): control (full kit, new mechanisms off) passed 3
of 15 (negative-control-004, every repetition), small profile 0 of 15; McNemar exact p 0.25; mean wall 95 s vs 30 s.
A sighting, not a result: greedy decoding makes repetitions near-copies, so n is nearer 5 than 15. Cause seen in the
log: the small arm made no tool call in any of its 15 model calls; the schema's explicit final branch lets the 3B answer
at once (it invents an answer without reading the file).

Not working or unverified: the small profile on the 3B (above); symlink case of the AGENTS.md check (no privilege to create
one here); MCP against a real server (only the fake in tests); hooks and MCP not run on POSIX; the console Rewind control
was exercised by test and not looked at in a browser.

Next three, in order:
1. Make the 3B act before it answers (e.g. no final branch before the first tool call unless the plan needs none, or a
   read-before-answer check) and rerun `bench/profile_run.py`; red-first, then re-measure.
2. Run the same pairing with `patch` on a larger local model and look at the Rewind control in the console.
3. Try an MCP server you actually use (`mcp_servers` in the config) and a pre_tool hook on `write_file`.

Waiting on the user: D26 credentials; the memory line a test wrote into the real memory
(`.ucephalus.ps1 memory --forget "injected instruction"`); a Mac or bare-metal Linux run; renaming the folder, then
moving its state directory (see Running it); the old venvs and `~/.alpha-build`. A real run already copied
`%LOCALAPPDATA%lpha-test-1` to `bucephalus` (the old one is untouched).
