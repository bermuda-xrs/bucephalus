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

Verified 2026-10-26 against commit `59fa20c` on `batch-17`: Windows suite, ruff, the frozen gate and a task through the
rebuilt `bucephalus.exe`'s window, and the WSL2 suite (see the Branch row for whether it reached `main`).

| Fact | Value | Regenerate with |
|---|---|---|
| Branch | `main`, fast-forwarded from `harness-14` (iteration 4, two whole-source reviews, the harness-16 baseline, the console split view, harness 17) after the suite, the frozen gate, the frozen e2e and the WSL2 suite passed; tags `iteration-4-closed`, `review-2-closed`, `h16-baseline`, `harness-17`; branch `h18` (rejected candidates) kept only as tag `h18-rejected` (2026-11-08); remote `origin` = private `bermuda-xrs/bucephalus` (`main`, the tags, and the results branches `cloud-8b`, `cloud-8b-2`, `cloud-8b-fam`, `cloud-8b-cfg` and `cloud-8b-cfg-2`, which carry `runs/` and are never merged) | `git branch --show-current` |
| Harness version | **20**, bumped 2026-11-08 for the console's Wave 1: measurement tasks carry `origin='bench'` and schedules are sealed in the anchor; the agent's prompts, tools and gate are unchanged, so 19's numbers still compare. 19 (2026-11-05): the gate compares an absolute path inside the root as its relative form (CHANGELOG "Absolute paths"); 18 let `edit_lines` anchors through; prompts unchanged since 15; `kept` numbers that include E1 from 18 and earlier do not compare, nor do numbers from 16 and earlier | `grep HARNESS_VERSION src/agent/__init__.py` |
| Toolset version | **8** (search: globs at any depth, name search with a space searches contents) | `grep TOOLSET_VERSION src/agent/__init__.py` |
| Tests | not written down | `python -m pytest tests -q`; skips are live-credential tests (D26) and other-OS tests |
| Lint | bug rules only | `python -m ruff check src tests scripts packaging demo.py` |
| CI | `.github/workflows/ci.yml`: ruff and the parallel suite on GitHub's Windows, Ubuntu and macOS runners, Python 3.11, every push and PR; first green 2026-10-28 (`bermuda-xrs/bucephalus#2`). The dev extras cap pytest below 9.2, which silently drops every `xdist_group` (`tests/test_xdist_groups_hold.py`) | the repository's Actions tab |
| Open issues | `docs/DEFERRED.md`, open entries only | its state table |
| CLI | 30 subcommands, each smoke-tested | `.\bucephalus.ps1 --help` |
| Platforms | Windows. POSIX has run six times, in WSL2 (`docs/evidence/wsl-2026-10-12/` is the last full checklist; the suite passed again 2026-10-25). Since 2026-10-28 CI runs the suite on GitHub-hosted Ubuntu and macOS VMs: macOS (`src/agent/platform/darwin.py`) first ran there and needed four fixes (zombies, orphan adoption, commit ratio, a 35 s reverse DNS lookup; `bermuda-xrs/bucephalus#2`). Bare-metal Linux never, and the PLATFORMS checklist has never been run on a Mac | `docs/PLATFORMS.md` section 5 is the checklist |
| Security posture | the capability gate is the boundary; the sandbox (Job Object limits, advisory path/network fence) constrains a mistaken agent, not a hostile one: D32 | |
| Phase program | complete | `RELEASE.md`, "ITERATION TWO: COMPLETE" |
| Running now | nothing | `Get-Process python, ollama -ErrorAction SilentlyContinue` |
| Uncommitted | nothing | `git status --porcelain` |

Workstyle: code carries no docstrings and almost no comments; the why is in commit messages
and failing tests. Frozen docs (never edited): FINDINGS, SPEC-DELTA, INVARIANTS, RELEASE,
PLATFORMS, AUDIT files. Live: this file, `docs/DEFERRED.md`, `CHANGELOG.md`.
Before any commit: tests red first, never weaken a check. Changes reach `main` through a pull request
whose `ci` run is green on all three OSes, never by a direct push; a results branch carries only `runs/`
(gitignored, so added with `git add -f`; such a push skips CI) and is not merged. Delete a branch once
it is merged.

## Next step

Iteration 4 (harness 15, CHANGELOG "Harness 15") measured every item on held-out generated families
(`runs/iter4-heldout-20261015`, 3B, n = 12 paired instances per item over 2 families, n = 48 for the base). On for the
small profile: C1 evidence pins and C3 ceiling 0.5 (multipage 0/6 -> 6/6 and 5/6), B2 carry and B6 compact prompt
(same passes, fewer tokens), stuck detection, the escape diagnostic, E1 hashline edits. Off: B1 (was on), B3, B4
(11 -> 3), B5, C2, the evaluator pass, the tool list. F (isolation) was not built.

Harness-16 baseline (`runs/h16-heldout-20261025`, 3B, held-out seeds 13-39 that no arm had seen, n = 112 paired
instances over 8 families, 14 each): base 54/112, all kept items together 66/112 (18 only kept, 6 only base, McNemar
exact p = 0.023), with a smaller prompt in every family. They add up on multipage (0/14 -> 11/14) and write_function
(10/14 -> 14/14), but multi_file falls 11/14 -> 7/14; only D2 had been measured on multi_file alone. Same on needle and
extract_json (14/14), unanswerable (5/14), config_edit (0/14); arithmetic 0/14 -> 1/14.

Read (2026-10-26, no code): config_edit fails because the model never edits. All 58 failed runs, every arm, call
only read_file, then answer with the path; the held-out grader says only "failed", so it re-answers to a plateau.
arithmetic fails on mental arithmetic: 21 failed runs never call run_command; median error 61%.

Not working: multi_file with the kept items together; config_edit and arithmetic on the 3B; the desktop app on Linux
without GTK or Qt; D78 and D80.

Needs the user: cloud compute for the 8B arithmetic run (plan item 1; 7.23 GB, 4.69 GB free here); D26 credentials;
a Mac for the PLATFORMS checklist (CI runs only the suite there) or bare-metal Linux.

multi_file leave-one-out (2026-10-26, same seeds, n = 14 per arm): base 11, kept 7; kept without B2 7, B6 7, C1 6,
C3 6, the stuck check 9, the escape check 7, E1 10. No single item restores it; two search traps do: glob='*' matched
only top-level files, and search(names=true, pattern='def name') matched no file name, after which the model answered
the example path from the question. Harness 17 fixes both in the tool (TOOLSET_VERSION 8); kept multi_file 7 -> 13/14
with 30% fewer prompt tokens, base 11/14 (`runs/h16-searchfix2-20261026`).

Harness 17 items, held-out seeds 13-39, n = 14 per family, against base (`runs/h16-heldout-20261025`): F1 `edit_first`
(hold the answer on a program-checked task until a file changed) config_edit 0 -> 9, write_function 10 -> 13, ON for
the small profile. F2 `compute_first` (hold a numeric answer until a script ran) arithmetic 0 -> 1 at twice the
tokens: the 3B cannot write and run a working script, OFF. The F1/F2 holds were designed from failures read on these
same seeds; the harness-17 baseline below confirms them on fresh seeds.

Harness-17 baseline (2026-10-27, dev seeds 0-26, n = 14 per family, `runs/h17-dev-20261027`): kept 81/112, base
54/112; kept alone passes 29, base alone 2, McNemar p = 5e-7. Per family, base -> kept: multipage 0 -> 14
(p = 0.0001), write_function 6 -> 13 (p = 0.016), config_edit 0 -> 5 (p = 0.06; 9 on the held-out seeds),
unanswerable 7 -> 8, multi_file 13 -> 12, arithmetic 0 -> 1, needle 14 -> 14, extract_json 14 -> 14. Prompt tokens
per family 0.4-0.9x base, except config_edit 2.4x (the edit hold's extra turns).

G1 `calc_tool` (branch `calc`, a tool for exact arithmetic on numbers and + - * / // % **, off by default), same dev
seeds against base: arithmetic 0 -> 1, extract_json 14 -> 14. The 3B called it in 1 of 14 arithmetic runs (the one
pass) and answered from its head in the rest. Not merged; branch `calc` deleted at the user's word.

Kept's failures on those dev seeds: config_edit 9/14 rewrite the whole file with write_file and lose or invent
settings (edit_lines is offered and never used); unanswerable 6/14 split into copying the sentence's full stop
("60 minutes." for "60 minutes") and answering a question the document does not answer, each resubmitted unchanged
until the plateau.

H18 candidates (branch `h18`, off by default, `SEEDS` 40 -> 80), on top of kept, fresh held-out seeds 41-67, n = 14
per family (`runs/h18-fresh-20261027`). H1 `write_diff` (an overwrite's receipt lists the lines it removed and added;
untrusted, it quotes the old file): config_edit kept 5 -> 6 (2 only kept, 3 only H1, p = 1), write_function 14 -> 14.
H2 `repeat_note` (an answer resubmitted unchanged after it failed, with no file changed since, is named as such):
unanswerable 4 -> 4, arithmetic 0 -> 0, the same runs passing; the note was sent and the model resubmitted the same
answer anyway. Both rejected; `h18` not merged. Kept against base on the same fresh seeds: config_edit 0 -> 5,
write_function 8 -> 14 (p = 0.03), unanswerable 5 -> 4, arithmetic 2 -> 0.

8B run (2026-10-28, a CPU-only cloud container, `bench/profile_run.py --suite bench/tasks --arms
control,full8b,small8b`, 3 reps x 5 tasks, branch `cloud-8b`, raw data `runs/cloud-8b/cloud-8b-full.tar.gz`):
control 4/15, full8b 9/15, small8b 9/15. Not a model comparison: `--out` was relative, so the config root was too,
and glob found no file in scope (full8b answered 0 on negative-control) and a path the harness showed was refused
when sent back (small8b could not read page 2 of the handbook). Fixed (CHANGELOG "Relative data dir"). The 8B's
other failure is its own: full8b re-read page 1 of 8 and invented ticket IDs. Temperature 0 was not deterministic
there: one prompt gave different code across reps.

8B re-run on the fix (2026-11-01, same command with `--out runs/cloud-8b-2`, code `917ab9b`, branch `cloud-8b-2`, raw
data `runs/cloud-8b-2/cloud-8b-2-full.tar.gz`): control 4/15, full8b 10/15 (1 crash), small8b 9/15; against control
p = 0.0703 and 0.2266, still a sighting (about 5 distinct tasks). The fix took effect: tool paths are absolute, full8b's
glob in the work folder finds the files, so negative-control passes in all 3 reps (was 0/3), and nothing inside it is
refused. Still failing: small8b's negative-control is `stuck`, searching only `.` (the run root, refused 15 times);
handbook-recall fails in every arm (small8b reads all 4 pages and names 1 of the 3 IDs, INF-6218, every rep; full8b
invents IDs). full8b rep 0's crash (extract-json, HTTP 500) and its write-function budget overrun came from a cold 8B
load, not the model: `profile_run` warmed only the 3B before timing (fixed, CHANGELOG "Warm each arm's model"). The
container was 30-60% slower per output token, so wall times and wall-clock budget statuses do not compare with the
first run (control's write-function ends `budget_exhausted`, not `max_steps`). A scan of the 232 run folders on this
machine found relative tool paths only in `profile-readfirst-20261011`; the h16, h17, h18 and iteration-4 numbers used
absolute roots and stand.

Families on the 8B (2026-11-03, branch `cloud-8b-fam`, code `1e593f8`, dev seeds 0-26, n = 14 per family, window
16384, budget 1800 s, raw data `runs/cloud-8b-fam/cloud-8b-fam-full.tar.gz`; the 3B on the same instances in
brackets): arithmetic base 4 [0], kept 2 [1]; config_edit base 14 [0], kept 0 [5]; unanswerable base 14 [7], kept 13
[8]; totals base 32/42 [7], kept 15/42 [14]. No crash, no instance beyond 700 s. The 8B on base settings beats the 3B
on every family (config_edit p = 0.0001, unanswerable 0.0156). Two findings: (a) arithmetic is answered from the
model's head, 28/28 instances called only `read_file` and never `run_command`; (b) under `kept` every `edit_lines`
call was refused by the gate (21 refusals, 14/14 instances): its anchors are copied from the read output, which the
taint tracker marks untrusted. Fixed in harness 18 (CHANGELOG "edit_lines anchors"); the 3B never called the tool, so
E1 was untested for it. The unanswerable failures left are the exact-match grader (`29 spaces.` with a full stop).
The cloud-8b-fam NOTES say the 3B scored 4-8/14 on unanswerable; on these instances it is 7 (base) and 8 (kept).

Config_edit on the 8B after harness 18 (2026-11-05, branch `cloud-8b-cfg`, code `5d082e2`, two attempts: the first
warm-up timed out loading the 8B, HTTP 500, no instance ran; the second loaded in 158 s and ran all 42 in 189 min; raw
data `runs/cloud-8b-cfg/cloud-8b-cfg-full.tar.gz`): base 14/14, kept 0/14 (all `plateau`), kept-E1 14/14. The anchor
fix did not make `edit_lines` usable: 26 gate refusals in 14/14 kept instances (21 before), 4 calls went through and
all four failed verification. Without E1 the 8B passes, so hashline editing is what costs it 14 instances. The refused
calls' arguments were not logged, so the cause of the rest is unknown; a replay on the real read output shows three
shapes still untrusted: an absolute path copied from the read output, an anchor with the line text appended
(`8:f5|on_hour = 52`), and content restating a line already in the file. The harness-18 claim that the anchor was the
cause is true for one shape only. `human.denied` now logs the call and the untrusted arguments (CHANGELOG "Denied
calls say why").

The denied-call log (2026-11-05, branch `cloud-8b-cfg-2`, code `37651ae`, two attempts: the first warm-up timed out,
the second ran all 28 in 132 min; archive SHA-256 `347bf5ee...a7e9`): base 14/14, kept 0/14 again. All 26 kept
refusals are `edit_lines` with `untrusted_args == ["path"]`, none on `start`, `end` or `content`, and in every one the
path is the absolute one (`/home/user/.../work/garden.ini`) that the `read_file` output header prints
(`{"ok": true, "path": "/home/user/..."}`); the 6 calls that got through used `work/garden.ini`. `known_paths` held only
the relative form, and the absolute form is also a token of the untrusted read output. Fixed in harness 19: an absolute
path inside the root is compared as its root-relative form (CHANGELOG "Absolute paths"). The other two shapes
(anchor with text, content restating a line) did not occur. The 4 accepted calls in cloud-8b-cfg and the 4 here that
changed the file still failed verification; why is not yet looked at.

Harness 19 on the 8B (2026-11-05, branch `cloud-8b-cfg-3`, code `547f4b0`; `base` copied from `cloud-8b-cfg-2`, harness
18; archive SHA-256 `f96c8810...0340`): kept 0/14 again, all `plateau`, but with 0 gate refusals (26 before) and 19 of
22 `edit_lines` calls on an absolute path, all allowed. The cause is now the model's use of the tool. 14 of 22 calls
failed with "anchor ... does not match the file now" (5 with a bare line number and no hash, 9 with a wrong hash). Of
the 8 applied edits (diffed against the first read of each file): 3 made one of the two required edits and never the
second (score 0.25), 3 replaced the wrong line by one (a `[lights]` header or `on_hour` for `off_hour`), 2 pasted
multi-line content that duplicated sections or put junk over a header. Not looked at: whether the model re-read after
an anchor error, and what the grader checks beyond the file. Hashline editing costs the 8B all 14 instances, with
`kept-E1` at 14/14, after two gate fixes.

Decision (2026-11-05, the user): E1 is dropped for the 8B. In the product the 8B is the reviewer, which runs the full
profile, so nothing there changes. In measurements the 8B runs `kept-E1`, never `kept`; 3B runs stay on `kept`. The 3B
never called `edit_lines` in any run, so E1 has not been shown to help any model.

Plan, in order:
1. Nothing else needs compute until the user's GPU machines are ready (up to 5 days, offered 2026-11-05). The question,
   fixed before the run: does the 8B with the kept items minus E1 (`kept-E1`) beat its own `base`? Both arms on all 8
   families, dev seeds, n = 14 (112 instances, 224 runs), window 16384, `--budget-seconds 1800`; pooled McNemar exact
   on `kept-E1` vs `base`, per-family counts reported next to it, no other arm and no re-cut afterwards. The odd
   (held-out) seeds stay unused for a confirmation. It also decides the 8B-as-default question this machine cannot
   test. Many machines: each runs `bench/families_run.py ... --shard K/N --out <its own folder>` (same `N` everywhere;
   a shard holds every N-th instance, with both arms of an instance on one machine), then
   `bench/families_merge.py <folders> --out <pooled>` writes the pooled jsonl and summary and refuses an instance
   measured in two shards. Each shard's `cards.json` records its model-server version and quantization; the merge
   keeps them in `shard_cards`, so mismatched setups show. Unanswerable is `family_version` 2 for this run (the
   answer ends its own line, no full stop; CHANGELOG "Unanswerable full stop"), so its numbers do not compare with
   earlier unanswerable numbers; every other family's instances are unchanged.
2. Arithmetic on the 8B stays open: it never calls `run_command`; a script hold (F2), a calculator (G1) and a repeat
   note (H2) did not move the 3B, so a nudge for the 8B is a new candidate, not a family.
3. The console takes the look of the official Hermes Agent UI (NousResearch/hermes-agent, MIT; the user's choice
   2026-11-07): restyled in our plain JS, no React, no build, no npm, their brand fonts and names not copied, the CSP
   unchanged. `tests/test_console_reskin_guards.py` holds every route, control, pane, view and slash command the
   console had, refuses a call to a route the server lacks, and holds WCAG AA. Done: the dashboard shell and palette
   (nav sidebar, Control Center panes as pages, Hermes Teal / blue light); the chat thread after their desktop app's
   chat (pinned request bubble, prose reply, quiet tool rows, step rail, floating composer); the pages in their page
   patterns, plus Sessions, Files and Models pages over existing routes only. The pages we lack follow one approved
   plan (2026-11-08) in four waves by risk: 1 Analytics, Docs, Sessions/Files/Schedules gaps; 2 System, MCP read-only;
   3 config editor with a sign step, write-only keys, Telegram pairing; 4 loopback webhooks. Plugins, the skills hub,
   MCP install, MoA, cloud providers, update/restart/restore and anything that sends data out are not built.
   Wave 1 done: Analytics (measurement tasks carry `origin='bench'` and are left out), Docs (read only, source
   checkout), bulk archive, new folder and removing a file you added, and schedules that pause, resume, run now,
   edit as a new record and are sealed in the anchor. Wave 2 done: System, MCP read-only, the reviewer model. Wave 3: Keys page and Telegram sender allow done; the config editor is left, HARNESS_VERSION 21 with it.

The branches already in `main` were deleted on 2026-10-27 at the user's yes; the tags stay.

History rewritten 2026-10-28 before the first push, every branch and tag, to drop `.alpha_token` (an old
local console token) and a `.venv` committed once: every commit ID changed. Commit IDs in this file and CHANGELOG are
remapped; the frozen docs quote the old ones. The old repository and the old -> new map are in
`OneDrive\Desktop\Βουκεφάλας\backups` (`bucephalus-pre-rewrite-20261028.bundle`, `bucephalus-rewrite-map-20261028.txt`).
