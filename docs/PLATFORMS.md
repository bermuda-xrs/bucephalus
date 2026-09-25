# Platforms: what runs where, and how to verify a new machine

**Windows 11 is the only platform anything in this project has run on.** Every
line below marked "never run" is code that exists and has not executed once.
Mac and Linux are wanted; this page is the preparation, and none of it is
verification. Written 2026-09-25 (desktop-app branch).

## 1. Inventory (4A)

Every OS-specific operation now lives in `src/agent/platform/` (section 4).
`windows.py` has run; `posix.py` serves Linux AND macOS and has never run.

| concern | Windows | Linux | macOS |
|---|---|---|---|
| **Sandbox resource limits** | Job Object, child created suspended then assigned: 512 MB per process, CPU = the timeout, 32 processes. No file-size limit exists on Windows. **Ran**: `test_sandbox_limits.py`, and the frozen gate 3 of 3 | rlimits AS / CPU / NPROC / FSIZE in a `preexec_fn`. **Never run** | Same code. `RLIMIT_AS` is not enforced, and a small value may be refused outright (section 3). **Never run** |
| **Sandbox fences** (write/read audit hook, SQLite authorizer, protected paths) | **Ran**, unfrozen and frozen | Pure Python, but path semantics differ (case sensitivity, `/proc`, symlinked temp dirs). **Never run** | As Linux, plus `/tmp` → `/private/tmp` symlinks. **Never run** |
| **Process-tree kill** (`kill_tree`) | `taskkill /T /F`. **Ran**, including both trampoline incidents | `killpg(getpgid(pid))`. **Never run, and wrong** (section 2) | Same code, same defect. **Never run** |
| **Process table**, parentage, start time (reaper, desktop shutdown) | Toolhelp32, `GetProcessTimes`. **Ran** | `/proc`. **Never run** | **Absent.** macOS has no `/proc`, so these return nothing. The orphan reaper and the desktop's shutdown sweep see no processes. Never run |
| **Memory metrics** (preflight, commit ratio, worker RSS) | ctypes. **Ran** | `/proc/meminfo`, `statm`. **Never run** | Absent (None / {}) |
| **The venv trampoline** | A venv `python.exe` is a launcher that starts the real interpreter as a child. Handled by the suspended-into-job spawn. **Ran** | Not applicable: a venv python is a symlink | Not applicable |
| **Multiprocessing start method** | Not used by the product. `freeze_support()` is called in the frozen dispatcher for libraries. **Ran**, frozen | fork by default (3.14: forkserver). Unused | spawn. Unused |
| **Frozen interpreter** (D13, `agent/frozen.py`) | **Ran**: probe gate 3 of 3, and from a windowed parent | Code is platform-neutral. **Never run** | `sys.executable` is `alpha.app/Contents/MacOS/alpha`. **Never run** |
| **File lock** (`runlock`) | `msvcrt` byte lock. **Ran** | `fcntl.flock`. **Never run** | Same. Never run |
| **Desktop window** | pywebview on EdgeWebView2. **Ran**, frozen | GTK WebKit2 or Qt, from system packages. **Never run** | WKWebView through pyobjc. **Never run** |
| **Packaging** | PyInstaller onedir, windowed, 188 MB. **Ran** | Onedir tarball or AppImage. **Never run** | `.app`, then codesign, notarization and hardened runtime. **Never run** |
| **State directories** | platformdirs (`%LOCALAPPDATA%`). **Ran** | `~/.local/share/alpha-test-1`. Never run | `~/Library/Application Support/alpha-test-1`. Never run |
| **Helper scripts** | `setup.ps1`, `ollama-serve.ps1` / `.cmd` | None exist (need `.sh`) | None exist |
| **Platform tests** | `test_sandbox_limits.py` | **No POSIX limit tests exist.** That file skips off Windows | Same |

## 2. The POSIX sandbox has never executed (4B)

Every sandbox finding in this project is Windows evidence: D13, D37, C41, the
SQLite routes, the read fence, the frozen gate. **The first run on a Mac or a
Linux machine is a new security audit, not a port.** Reading the code already
finds these, before anything has run:

1. **`kill_tree` kills its caller.** It sends SIGKILL to the target's process
   GROUP, and nothing starts the sandbox's children in a group of their own.
   So the group is the caller's, and the agent, or the desktop app, kills
   itself. Fix: `start_new_session=True` on every spawn, and never `killpg`
   the caller's own group.
2. **No process table on macOS.** The reaper and the desktop's shutdown sweep
   see nothing, silently. Fix: libproc (`proc_listallpids`,
   `proc_pidinfo(PROC_PIDTBSDINFO)`), or `ps -axo pid=,ppid=,comm=`.
3. **`preexec_fn` in a threaded process.** Python documents it as unsafe to
   use with threads, and the control plane and desktop app are threaded. Fix:
   apply the rlimits inside the child, first thing in the guard preamble,
   before any user code. Lowering a hard limit can't be undone from inside.
4. **`RLIMIT_NPROC` is per user, not per sandbox.** A desktop user with more
   than 32 processes gets a sandbox that can't start a single subprocess.
   Windows' job count is per job. Fix: count inside the process group (a
   watchdog), or use a cgroup's `pids.max` on Linux.
5. **`RLIMIT_AS` on macOS**: absent or fatal (section 3).

**Tests that must pass on the new machine before its sandbox is trusted:**

- `tests/test_m5_action.py`: scope refusals, the tool bridge, the gate.
- `tests/test_sqlite_routes.py`: the authorizer and every `file:` URI spelling.
- `tests/test_sandbox_libraries.py`: sympy inside the sandbox.
- `tests/test_frozen_sandbox.py`: unfrozen half here, frozen half in the probe.
- `tests/test_every_verifier_can_fail.py`: runs sandboxed pytest.
- `tests/test_desktop.py`: window close leaves no process (defect 1 lands here).
- `tests/test_runlock.py`, `tests/test_reaper_parentage.py`.
- **POSIX limit tests, which do not exist yet and must be written first,**
  mirroring `test_sandbox_limits.py`:
  - memory past the cap fails
  - CPU past the limit is killed
  - processes past the limit are refused
  - a file larger than 64 MB is refused (POSIX has this limit; Windows doesn't)
  - the timeout kills grandchildren
  - `kill_tree` does not kill its caller
  - limits apply when the caller is threaded
- The frozen probe gate on that OS (checklist, step 6).

## 3. macOS memory cap (4C)

**Research.** Two sources agree:

- Darwin doesn't enforce `RLIMIT_AS` / `RLIMIT_DATA` on the allocations that
  matter.
- It rejects values below a process's current virtual size, and a macOS
  process starts with hundreds of GiB already reserved. So any cap small
  enough to be real is refused (EINVAL).

Here that means one of two things: `setrlimit` raises inside `preexec_fn`
and every run fails closed (now a `LimitsRefused`), or it is accepted and
does nothing. The Linux path does not cover the Mac. Sources:
[srelens#713](https://github.com/srelens/srelens/issues/713),
[imandrakit#26](https://github.com/imandra-ai/imandrakit/issues/26),
[llm-cad#38](https://github.com/MaximumTrainer/llm-cad/issues/38).

**Proposal for `darwin.py`**, not built. It is new code, and it cannot be
verified here:

1. **No `RLIMIT_AS` on Darwin.** Keep `RLIMIT_CPU` and `RLIMIT_FSIZE`, which
   Darwin does enforce.
2. **Start every sandbox child in its own session** (`start_new_session=True`),
   so its tree is one process group this process doesn't belong to.
3. **A watchdog thread per run.** Every ~100 ms it samples
   `proc_pid_rusage(pid, RUSAGE_INFO_V4).ri_phys_footprint` for each process
   in that group. This is the counter macOS's own memory-pressure system and
   Activity Monitor use; libproc is available through ctypes. Past 512 MB,
   `killpg(SIGKILL)` the child's group and report "memory cap exceeded".
4. **Say what it is.** Reactive, not kernel-enforced: a child can overshoot by
   one interval's allocations before it dies. The hard guarantees on macOS are
   the wall-clock timeout and the group kill, and `limits_note()` must say so
   in every result, the way the Windows note names the missing file-size
   limit.
5. **When a hard cap is required**, run the sandbox in a Linux VM or container
   (cgroup `memory.max`). The sandbox's own docstring already names this
   (Docker, `--network=none`).

**Linux, for comparison.** `RLIMIT_AS` works but counts virtual memory, which
is strict on mmap-heavy libraries. The physical-memory equivalent is cgroup v2:
`systemd-run --user --scope -p MemoryMax=512M -p TasksMax=32`, where a user
systemd session exists. `TasksMax` also fixes defect 4.

## 4. The platform layer (4D), built

This is a refactor of code that already existed, so it is built.
`src/agent/platform/`:

- `__init__.py` names the contract, `INTERFACE`: 12 functions for processes,
  limits and file locks. It chooses `current` once: `windows` on win32,
  `posix` otherwise.
- `windows.py`: the pre-refactor Windows branches, moved verbatim. Has run.
- `posix.py`: the pre-refactor POSIX branches, moved verbatim, with the
  defects above in its header. Never run. One deliberate change: a limit the
  kernel refuses is now a `LimitsRefused` (fail closed, the code doesn't run)
  instead of an uncaught crash.
- `agent/proc.py`, `agent/runlock.py` and `agent/execute/sandbox.py` call
  `current.*` and keep their public names.
- `tests/test_platform_layer.py`:
  - every platform module defines the whole contract (parsed, not imported,
    so `posix.py` is held to it on Windows);
  - no `sys.platform` / `os.name` / `IS_POSIX` check exists outside the
    layer. The scattered form it replaced had 13 of them.

**A port is then:**

- `darwin.py`: `posix` plus a libproc process table, start time and memory,
  and the section 3 watchdog in `spawn_limited`.
- Fixing defects 1, 3 and 4 in `posix.py`.
- Adding one line to the selector in `__init__.py`.

## 5. Checklist: verifying a new machine (4E)

Run in order. Save every command's output under
`docs/evidence/platform-<os>-<date>.txt`. **Stop at the first STOP.**

```bash
# 0. What this is
python3 --version            # must be 3.11.x
uname -a

# 1. A fresh checkout and a venv OUTSIDE any synced folder
git clone <repo> alpha && cd alpha && git checkout desktop-app
python3 -m venv ~/.alpha-venv && . ~/.alpha-venv/bin/activate
pip install pydantic httpx anthropic pyyaml fastapi uvicorn scipy rich pytest \
            pytest-asyncio "sympy==1.13.3" platformdirs
export PYTHONUTF8=1

# 2. Smoke: every CLI subcommand starts
python -m pytest tests/test_cli_subcommands.py -q

# 3. The whole suite. Record passed / failed / skipped.
python -m pytest tests -q -rs
#    Every SKIP that names a platform is a property unverified on this machine.
#    test_sandbox_limits.py skips off Windows: POSIX has no limit tests yet.

# 4. The known defects, one probe each. Run INSIDE setsid so defect 1 cannot
#    kill your terminal.
setsid python - <<'EOF'
import subprocess, sys, time, os
sys.path.insert(0, "src")
from agent.proc import kill_tree, process_table
print("process table rows:", len(process_table()))          # 0 on macOS = defect 2
c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
open("/tmp/alpha_probe_alive", "w").write("before")
kill_tree(c.pid)                                             # defect 1: may kill THIS
open("/tmp/alpha_probe_alive", "w").write("survived")
EOF
cat /tmp/alpha_probe_alive                                   # "before" = defect 1 is live

python - <<'EOF'
import sys; sys.path.insert(0, "src")
from agent.execute.sandbox import run_sandboxed
r = run_sandboxed("b = bytearray(640 * 2**20); print('allocated')", timeout_s=60)
print(r["returncode"], r["stdout"].strip(), r["stderr"][-200:])
EOF
#    "allocated" = NO MEMORY CAP. Expected on macOS until darwin.py exists.
#    "limits could not be applied" = every run fails closed (RLIMIT_AS refused).
```

**STOP** if step 4 shows defect 1 live, or if `allocated` printed on Linux.
The sandbox is not trustworthy on this machine until the fixes in section 2
land, with the POSIX limit tests written and green.

```bash
# 5. The sandbox property suites
python -m pytest tests/test_m5_action.py tests/test_sqlite_routes.py \
  tests/test_sandbox_libraries.py tests/test_frozen_sandbox.py \
  tests/test_every_verifier_can_fail.py tests/test_desktop.py \
  tests/test_runlock.py tests/test_reaper_parentage.py -q

# 6. The frozen gate, in a separate build venv
python3 -m venv ~/.alpha-build-venv && . ~/.alpha-build-venv/bin/activate
pip install . pyinstaller pytest pytest-asyncio pywebview "sympy==1.13.3"
python packaging/build.py --probe
~/.alpha-build/dist/alpha-probe/alpha-probe     # expect: all passed, 1 skipped
ps -ef | grep -c "[a]lpha-probe"                # expect 0 afterwards

# 7. The desktop app
python packaging/build.py
ALPHA_CONFIG_DIR=/tmp/alpha-cfg ALPHA_DATA_DIR=/tmp/alpha-data \
  ~/.alpha-build/dist/alpha/alpha &             # macOS: open the .app
# close the window, then:
ps -ef | grep -c "[a]lpha"                      # expect 0
ls /tmp/alpha-data                              # state landed here, not the checkout
```

Anything that fails is a finding for FINDINGS, recorded with its output. It is
not a test to adjust until it passes.
