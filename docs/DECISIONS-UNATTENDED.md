# Decisions made on the user's behalf - iteration 3 (unattended, from 2026-09-28)

Each entry: the question, the options, the choice, why, and how to reverse it.
"WAITING FOR USER" marks a choice that was irreversible and ambiguous, so it
was not made.

## U1 - Which state leaves a checkout that sits in a synced folder (1.1)

- **Question.** "State must never live inside a synced folder." Which state?
- **Options.** (a) move the whole state root to platformdirs; (b) move only
  runtime state: database, anchor, `runs/` records the product writes; keep
  tracked content (`grading/`, `skills_store/`) and the task workspace `work/`.
- **Chosen: (b).** (a) strands the 30 tracked graders and 12 seed skills, and
  breaks `bench/` from a checkout. `work/` holds the user's own task output.
  The database (every goal, answer and tool output) and the anchor that
  authenticates it are what must not be uploaded.
- **Existing files are not moved.** `agent.db`, `agent.local.db` (the
  283-task measurement record) and `anchor/*.anchor` stay in the checkout and
  stay synced until the user moves them; a one-time notice names them. Moving
  a measurement record is not a call to make unattended.
- **Consequence to know about.** `alpha` run from this checkout now starts on
  an empty database in `%LOCALAPPDATA%\alpha-test-1\alpha-test-1\checkouts\
  alpha-test-1-<hash>\`. The desktop app is unaffected (it always used
  platformdirs).
- **Reverse.** Set `ALPHA_DATA_DIR` to the checkout, or copy the old database
  into the new directory. Code: `config._synced_state_dir`.

## U2 - The operator's real memory holds test content: WAITING FOR USER

`%LOCALAPPDATA%\alpha-test-1\alpha-test-1\memory\MEMORY.md` contains
`- [user] injected instruction`, written 2026-09-25 by the audit's probe (the
suite did not isolate the user data dir; fixed in 38edfa1). It is in the
prefix of every real session since. The run's rules forbid touching real
memory files, so it is left. To remove it:
`alpha memory --forget "injected instruction"` (then `alpha memory` to check).
