# Linux, WSL2 Debian, Python 3.13.5 - 2026-10-12

`docs/PLATFORMS.md` section 5 in full, in a fresh clone outside the Windows filesystem, by `run.sh` (run1 wrote to
`~/buc-evidence`). Not bare metal.

| step | run1 (`6ac17dc`) | run2 (`3d7ca8e`) |
|---|---|---|
| 1 install | ok | ok |
| 2 CLI smoke | 50 passed | 50 passed |
| 3 suite, `-n 8` | 1 failed (HANDOVER named a gitignored path) | 2 failed: that, and the HttpTransport pin (`ast.dump` differs between 3.11 and 3.13; fixed in `94ff866`) |
| 4a kill_tree spares its caller | yes | yes |
| 4b sandbox memory limit | 640 MB refused (`MemoryError`) | same |
| 5a POSIX platform tests | 22 passed, 1 skipped (macOS) | same |
| 5b sandbox and route suites | 158 passed | 158 passed |
| 6 frozen probe gate | **1 failed**: no recursive spawn found, because `/proc/<pid>/stat` cuts names at 15 characters (`bucephalus-prob`) | **39 passed, probe exit 0** (names read from `cmdline`, `46c6a40`) |
| 7 desktop app | builds (63 MB); exits 1: no GTK or Qt in this WSL | same |

Step 7 is the environment: pywebview needs GTK (`python3-gi`, WebKit2GTK) or Qt from system packages. The app's state
directories were created before it exited, and no process was left behind.
