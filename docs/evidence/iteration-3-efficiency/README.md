# Efficiency, before and after - 2026-09-29 (iteration 3, Part 3 and 4.8)

"Before" is `main` at 1d911cc, "after" is `iteration-3`. Nothing else ran
during a timed run; before and after alternate within each round, so drift in
the machine lands on both. This machine's own drift is large (DECISIONS U10:
the same commit's suite took 47 s one day and 155 s the next).

## Unfrozen, 5 rounds - `part3-before-after.json`

| | before | after |
|---|---|---|
| `verify_all` over a copy of the 283-task record (the console polls it) | 0.918 s | **0.290 s** |
| per agent step, 40-step scripted task (harness time only) | 33.3 ms | 31.9 ms |
| `alpha serve` to /health, mock models | 2.63 s | 2.58 s |
| `alpha --help` | 1.46 s | 1.44 s |
| `alpha serve` idle: working set / private | 62 / 45 MB | 62 / 46 MB |

The sweep: two thirds of it was re-reading the anchor file twice per task,
one of the two reads unused (D72, partly fixed). `serve`'s startup is half
FastAPI's own import (1.2 s, `-X importtime`); unchanged. The first `serve`
figures (3.08 s both) were an artifact of the probe: on Windows a connect to a
port nobody listens on yet takes ~2 s to be refused, so the probe now uses a
0.1 s connect timeout.

**Database (3.5):** WAL, `synchronous=FULL` (write-before-act needs the
fsync), FTS5 for search, and every query the new console runs is served by an
index (`EXPLAIN QUERY PLAN` on a copy of the record) - nothing to add.

## Frozen desktop app - three trials each

Old build (`main`, 2026-09-25) against the new one, interleaved
(`frozen-close-old-vs-new.json`), then the final HEAD build on its own
(`frozen-close-idle-head.json`, `frozen-task-through-window-head.json`):

| | old | new |
|---|---|---|
| spawn to /health | 2.51 s | 2.23 s |
| spawn to first paint in the window | 4.59 s | 4.55 s |
| idle, whole tree (app + 6 WebView2): working set / private | 501 / 280 MB | 512 / 283 MB |
| during a task | - | 524 / 300 MB |
| close (WM_CLOSE) to the last process gone, idle | 2.99 s | 2.63 s; HEAD 2.56 s |
| close after a task | not measured | 22.3 s, then **3.4 s** (D81) |
| processes left after close | none | none |
| build size | 63 MB | 62 MB |

**The scripted task (4.8)** ran through the window's own controls over
DevTools on a scratch port: text typed into the composer, "Draft a plan"
submitted, the plan card's "Sign and run" clicked, the answer read from the
page (`docs/evidence/ui/frozen-app-task-done.png`). Mock models, scratch data
directories; the model server on 11434 was never touched.

**Where a close goes (3.2)**, unfrozen, with a timestamp at each stage: the
window hides at +0.04 s, the tree is killed by +0.28 s, the database is closed
and `main()` returns by +0.42 s. The process object is gone at +2.5 s; the
difference is the OS tearing down the WebView2/CLR host, and terminating self
instead of `os._exit` measured no faster. **The 2 s target is not met** for
the process to disappear; the app's own work is done in under half a second.

**An instrument error, corrected:** the first frozen runs reported one
"orphan" in two of three closes, in the old build and the new. It was an
exited process whose start time stays readable while any handle to it is open;
alive is now judged by exit code, as the product's own `proc._alive` does.
