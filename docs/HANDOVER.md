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
| Harness version | **11**, bumped 2026-10-12 for the planner survey rule (CHANGELOG "Harness 11"); numbers from 10 and earlier do not compare | `grep HARNESS_VERSION src/agent/__init__.py` |
| Toolset version | **7** | `grep TOOLSET_VERSION src/agent/__init__.py` |
| Tests | not written down | `python -m pytest tests -q`; skips are live-credential tests (D26) and other-OS tests |
| Lint | bug rules only | `python -m ruff check src tests scripts packaging demo.py` |
| Open issues | `docs/DEFERRED.md`, open entries only | its state table |
| CLI | 29 subcommands, each smoke-tested | `.\bucephalus.ps1 --help` |
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

STOPPED BY THE USAGE LIMIT mid-batch, on branch `harness-10` (NOT merged; main is still the harness-9 state). The
suite, the frozen gate and the frozen e2e were NOT rerun on this branch; do that before any merge.

Done and committed on `harness-10`: 1a the real memory line forgotten (backup first; blessing intact; old state dir untouched);
1b old venvs and ~/.alpha-build deleted; 1c keep-awake on every timed runner; 2 runs record the code identity and the
prompt cannot change without a version bump (HARNESS_VERSION is now 10); v9 paired run on the 3B (valid, no standby):
control 3 of 15, small+read_first 6 of 15, McNemar p 0.51, wall 74.7 s vs 76.4 s; 3 diagnosis (below); 4 real
mcp-server-time and mcp-server-git plus a pre_tool hook end to end (`tests/test_mcp_real.py`, opt-in); 5a diff view, 5b `bucephalus say`,
5c Telegram queue (FakeTransport only), 5d JSON/YAML/TOML/JS diagnostics, 5e expandable tool cards; the MCP gate fixes from two
automated reviews; 9 the 8B arms and a memory preflight in `bench/profile_run.py` (not yet committed: run
`git status`, then commit `bench/profile_run.py` and `tests/test_profile_run.py`).

Diagnosis (3): write-function-002 could never pass in either arm (the runner's scratch root held no graders: fixed);
handbook-recall-005 hid 60% of the file behind an `expand_artifact` pointer the small kit lacked (fixed: sixth tool and a
window-sized cap); what remains is the model (it wrote a regex with a NUL character and answered anyway). A rerun of the two
tasks on harness 10 was in progress (`runs/profile-two-20261012/`, started from a clean worktree under the session scratchpad:
`git worktree prune` removes it); its result is not read. One control run crashed on a dropped Ollama connection.

Not done: 6 Linux build and PLATFORMS section 5 in WSL2; 7 mutation testing and the suite wall time before/after; 8 the
independent audit; 10 rename script; 5f (D16 start check). Open review findings, not fixed: MCP argument names that
collide after sanitizing (two keys to one), and path strings with no separator or `file:` scheme under a non-path key.

Not working or unverified: the AGENTS.md symlink case; the Telegram queue and the console panels (Changes, tool cards, /say)
were tested but not clicked through in a browser.

Next three, in order: (1) commit the 8B prep, run the full suite, the frozen gate and e2e, and read the two-task rerun;
(2) the Linux build, then mutation testing, then the independent audit; (3) with about 7 GB free and the machine awake, run
`bench/profile_run.py --preflight --arms control,full8b,small8b`, then the run.

Waiting on the user: D26 credentials; a Mac or bare-metal Linux run; renaming the folder and moving its state directory (see Running it).
