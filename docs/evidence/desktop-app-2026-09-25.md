# Desktop app, frozen: what was run and what it showed - 2026-09-25

Branch `desktop-app`. Build venv `~/.alpha-test-1-build-venv` (project installed
non-editable, PyInstaller 6.22.3, pywebview 6.2.1 on WebView2), Python 3.11.15,
Windows 11. Output `~/.alpha-build/dist/alpha/`.

## Build

    python packaging/build.py            # windowed onedir, one command

- **188 MB**, against the 169 MB of C26's minimal probe. The difference is the
  whole standard library (bundled on purpose: the binary is the sandbox's
  interpreter for model-written code), sympy/mpmath, uvicorn/FastAPI and
  pywebview with its .NET bridge.
- `no_task_content()` passes: no `bench/`, no grader, no `verify.py`, no JSON
  shaped like a task. The build refuses itself if one appears.

## The first frozen build did not start

"Unhandled exception in script" before the window opened. Stepping through
`main()` in the binary's own interpreter mode, every step worked; the
difference is `--windowed`, where `sys.stdout` and `sys.stderr` are `None` and
uvicorn's logging calls `.isatty()` on them (`ValueError: Unable to configure
formatter 'default'`). Streams now go to `desktop.log` in the data directory;
`test_a_windowed_build_without_streams_still_starts` raises that exact error
with the fix removed.

## The app, run

Scratch `ALPHA_CONFIG_DIR` / `ALPHA_DATA_DIR`, launched from inside the
checkout (the case where state would otherwise land in the project):

| check | result |
|---|---|
| window | title `alpha`, the console rendered |
| bind | `127.0.0.1` only, an OS-chosen port; `/health` 200 |
| state | first-run `config.yaml` in the config dir; database, anchor, work in the data dir - not the checkout |
| children while open | 6 `msedgewebview2.exe` |
| window closed (WM_CLOSE, not a kill) | the app exited 8 s later; 0 children left; port released |

## A real task through the frozen app

Local profile, granite4.1:3b on the project's Ollama: create, draft a plan,
approve, run, over the app's own API. Done in 222 s. It did NOT exercise the
sandbox: the 3B planner returned unparseable JSON and the model answered
without calling `run_code` (FINDINGS 1.32), graded by `always_pass`.

## The sandbox from a windowed parent, deterministically

The frozen binary started DETACHED with no standard handles - a windowed
process's condition, confirmed inside it: `sys.stdin` and `sys.stdout` are
`None` - calling `run_sandboxed` itself:

| check | result |
|---|---|
| `print(sum(range(101)))` | returncode 0, `5050` |
| sympy solve | returncode 0, `[-2, 2]` |
| write outside scope | `PermissionError: sandbox: write denied outside scope`; the file does not exist |
| `while True: pass`, 4 s | `TIMEOUT` |

The full gate (`d13-freeze-gate-2026-09-25.txt`) ran from the console probe;
this is the same code path from the windowed condition, which differs in the
parent's handles - and that difference is the stdin defect D13's fix closed.

## After

Every process the app started was gone. Six `msedgewebview2.exe` still running
belong to Windows' `SearchHost.exe`, started the previous day. The project's
Ollama was stopped through `kill_tree`; the user's own instance on 11434 was
not touched.
