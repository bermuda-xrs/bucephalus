# Linux, WSL2 Debian, Python 3.13.5 - 2026-09-30

The suite at the branch head, `-n 8`, in a fresh checkout outside the Windows
filesystem (the method of `docs/evidence/wsl-2026-09-25/`).

| run | result |
|---|---|
| at 41af423, first | 24 failed: 20 of them this day's own D70 check refusing every POSIX directory (a directory has two or more links), plus D54, D55, D56 and a Windows-only test importing the Windows module |
| at c5881a0 | 5 failed: D54, D55 (x2), D56, and D77 on 3.13 |
| at 9fba01d | **1236 passed, 0 failed** (`suite-tail.txt`) |

Repeated, because D56 was intermittent (`wsl_d56.sh` in the session scratchpad):

| | failed runs |
|---|---|
| the two concurrent-connection tests, `cached_statements=0` | 0 of 20 |
| the same, the statement cache back on | **18 of 20** |
| `tests/test_reaper_parentage.py` (D55, and the daemon safety test) | 0 of 10 |
| `tests/test_desktop.py` (D54) | 0 of 10 |

Not run here: macOS (never run anywhere) and bare-metal Linux - the
checklist in `docs/PLATFORMS.md` section 5 is still for both.
