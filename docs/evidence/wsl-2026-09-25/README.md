# PLATFORMS.md section 5, run in WSL2 - 2026-09-25

Debian 13.5 on WSL2, kernel 6.18.33.2-microsoft-standard-WSL2, **Python 3.13.5**
(the only Python present; installing 3.11 needs sudo). Fresh clone of `main`
in the Linux filesystem; steps 2-5 at `5a6cdf8`, step 6 at `55cb423`.

**WSL is a real Linux kernel, not a normal desktop.** Process sessions, init
and resource-limit behaviour can differ from bare-metal Linux, and Python 3.13
is not the version this project pins. A pass here is strong evidence for the
Linux half of Phase A, not proof. macOS is untouched.

| step | result |
|---|---|
| 5a - `test_posix_platform.py` real-kernel half | **7 of 7 pass** (the 8th is macOS-only, skipped) |
| 4 - `kill_tree` does not kill its caller | survived |
| 4 - process table | 18 rows |
| 4 - 640 MB allocation | refused: `MemoryError` (RLIMIT_AS, kernel-enforced) |
| 5b - sandbox property suites | 148 passed, 4 failed |
| 2 - CLI smoke | 46 passed |
| 3 - full suite | 1015 passed, 8 failed, 27 skipped |
| 6 - frozen probe gate (Linux build, 265 MB) | 35 passed, 1 failed, 9 skipped (Windows-only) |

The failures are six distinct defects, filed as DEFERRED D54-D59.
