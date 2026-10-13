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

Verified 2026-10-13 against commit `4fa0dfc` on `harness-10`: Windows suite, ruff, the frozen gate and a task through the
rebuilt `bucephalus.exe`'s window (see the Branch row for whether it reached `main`).

| Fact | Value | Regenerate with |
|---|---|---|
| Branch | `main`, fast-forwarded from `harness-10` after the suite, the frozen gate and the frozen e2e passed; no remote | `git branch --show-current` |
| Harness version | **11**, bumped 2026-10-12 for the planner survey rule (CHANGELOG "Harness 11"); numbers from 10 and earlier do not compare | `grep HARNESS_VERSION src/agent/__init__.py` |
| Toolset version | **7** | `grep TOOLSET_VERSION src/agent/__init__.py` |
| Tests | not written down | `python -m pytest tests -q`; skips are live-credential tests (D26) and other-OS tests |
| Lint | bug rules only | `python -m ruff check src tests scripts packaging demo.py` |
| Open issues | `docs/DEFERRED.md`, open entries only | its state table |
| CLI | 29 subcommands, each smoke-tested | `.\bucephalus.ps1 --help` |
| Platforms | Windows. POSIX has run four times, in WSL2 (`docs/evidence/wsl-2026-10-12/` is the latest); bare-metal Linux never, and macOS (`src/agent/platform/darwin.py`) has never executed anywhere | `docs/PLATFORMS.md` section 5 is the checklist |
| Security posture | the capability gate is the boundary; the sandbox (Job Object limits, advisory path/network fence) constrains a mistaken agent, not a hostile one: D32 | |
| Phase program | complete | `RELEASE.md`, "ITERATION TWO: COMPLETE" |
| Running now | nothing | `Get-Process python, ollama -ErrorAction SilentlyContinue` |
| Uncommitted | nothing | `git status --porcelain` |

Workstyle: code carries no docstrings and almost no comments; the why is in commit messages
and failing tests. Frozen docs (never edited): FINDINGS, SPEC-DELTA, INVARIANTS, RELEASE,
PLATFORMS, AUDIT files. Live: this file, `docs/DEFERRED.md`, `CHANGELOG.md`.
Before any commit: tests red first, never weaken a check, nothing pushed.

## Next step

The 2026-10-12 brief is done except item 9 (the 8B run, prepared only).

Done: 1 the injected memory line forgotten, old venvs and `~/.alpha-build` deleted, keep-awake through the platform
layer; 2 runs record the code they loaded, the prompt cannot change without a bump, v9 paired run on the 3B (control 3 of
15, small+read_first 6 of 15, McNemar p 0.51); 3 diagnosis (below); 4 real MCP servers and a pre_tool hook end to end
(`tests/test_mcp_real.py`, opt-in); 5 diff view, `bucephalus say`, the Telegram queue (FakeTransport), diagnostics, tool
cards, the D16 window check; 6 WSL2, PLATFORMS section 5 twice (`docs/evidence/wsl-2026-10-12/`); 7 mutation
(`docs/evidence/mutation-2026-10-12/`); 8 the audit (`docs/AUDIT-2026-10-12.md`): P1 and the mediums fixed red-first,
the rest is D79; 10 the checkout renamed by `scripts/rename_checkout.ps1`, its state directory moved with it.

Diagnosis (3): write-function-002 had no graders under the runner (fixed). handbook-recall-005 hid the middle of the
file behind a tool the small kit lacked (sixth tool), and a cap above half the compaction ceiling then forced a session
boundary that dropped the handbook (fixed in harness 11). Rerun on harness 11 (2026-10-13, 3B, 3 reps): still none of
6 pairs passed in either arm. No session boundary now; the handbook is about 5.5k tokens against an inline cap of
16384 x 0.28 x 0.5, about 2.3k, so its middle is elided, and the 3B answers without paging it in (no `expand_artifact`
or `search` call). One small-arm run crashed on a connection reset by Ollama 8 s into a call; the record does not say
whether tokens had streamed (no retry then) or the retry also failed.

Not working: handbook-recall on the 3B (above); the desktop app on Linux without GTK or Qt (this WSL has neither);
D79's open findings (R2 fixed and M2 in part, 2026-10-13).

Needs the user: D26 credentials; the 8B run (`bench/profile_run.py --preflight --arms control,full8b,small8b`, about
7 GB free and the machine awake); a Mac or bare-metal Linux run.

Next three: (1) a decision for the user: let the small profile show a long file whole (a higher `context_fill_ratio`
for it) or have `read_file` return numbered pages the model is told to continue, then rerun the two tasks; (2) the 8B
run; (3) D79's rest: an environment allowlist for hooks and MCP servers (L1), a side-effect-free diff view (L4).
