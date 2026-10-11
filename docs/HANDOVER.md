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
| `bench/profile_run.py --suite bench/tasks` | ~35 min, 3B, control vs small profile; keep the machine awake (Modern Standby stalls it) |

One measurement process at a time (`src/agent/runlock.py`); nothing else runs during a timed run.

## State

Verified 2026-10-11 against commit `7a0eef1` on `harness-9`: Windows suite, ruff, the frozen gate and a task through the rebuilt `bucephalus.exe`'s
window (see the Branch row for whether it reached `main`).

| Fact | Value | Regenerate with |
|---|---|---|
| Branch | `main`, fast-forwarded from `harness-9` after the suite, the frozen gate (63) and the frozen e2e passed; no remote | `git branch --show-current` |
| Harness version | **9**, bumped 2026-10-11 for read_first in the small profile (CHANGELOG "Harness 9"); numbers from 8 and earlier do not compare | `grep HARNESS_VERSION src/agent/__init__.py` |
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

Done, merged to `main`: tool scripts (bench/, packaging/,
scripts/) refuse the real user directories unless `BUCEPHALUS_DATA_DIR` / `BUCEPHALUS_CONFIG_DIR` are set (a test walks
every entry point); `read_first` (no answer before the first observation; on for the small profile only); checkpoints only
before an action that changes something; the console Rewind control clicked through and three bugs fixed. The copy of
the state directory a smoke run made was deleted (it had one extra empty folder); the real first launch will migrate.

Measured (`bench/profile_run.py`, granite4.1:3b, `bench/tasks`, 5 tasks x 3 repetitions = 15 pairs, 2026-10-11,
`runs/profile-readfirst-20261011/paired.json`): control (full kit, new mechanisms off) 3 of 15, small + read_first 6 of
15 (extract-json and summarize-untrusted every repetition; control only negative-control); McNemar exact p 0.51.
Before read_first the small profile was 0 of 15. A sighting, not a result: n is nearer 5 tasks than 15 pairs. Wall clock
is invalid for repetition 1 of the small arm (the laptop entered Modern Standby three times); without it, mean 57 s vs
107 s (n = 10 per arm). Recorded with HARNESS_VERSION still reading 8; the code is that of 9.

Checkpoint cost (mock backends, 7 steps: 5 reads, 1 write, 1 answer; median of 5): 3.95 s with 7 checkpoints before,
0.86 s with 1 after, 0.22 s with checkpoints off.

Not done: the 8B. granite4.1:8b is 5.1 GB of weights plus the KV cache for num_ctx 8192; 4.0 GB of 15.5 GB was free
(commit 17.6 GB), held by applications this session did not start. Not working or unverified: symlink case of the
AGENTS.md check; MCP against a real server; hooks and MCP on POSIX.

Next three, in order:
1. Free about 7 GB (close the browser and other apps), keep the machine awake, and run 3B control vs 8B full vs
   8B small+read_first with `bench/profile_run.py` (add the two 8B arms).
2. Look at why small + read_first still fails write-function and handbook-recall (the per-arm databases sit beside `paired.json`).
3. Try an MCP server you actually use (`mcp_servers` in the config) and a pre_tool hook on `write_file`.

Waiting on the user: D26 credentials; the memory line a test wrote into the real memory
(`.\bucephalus.ps1 memory --forget "injected instruction"`); a Mac or bare-metal Linux run; renaming the folder, then
moving its state directory (see Running it); the old venvs and `~/.alpha-build`.
