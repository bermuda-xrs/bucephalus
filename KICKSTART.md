# Kickstart prompt

Paste the block below to a coding agent working in this directory. It assumes
the agent has filesystem access here and can run commands — it does not need
files attached, only pointing at.

---

You are picking up an in-progress research project at
`C:\Users\char1\OneDrive\Desktop\Βουκεφάλας\alpha-test-1`. It is a working
system, not a greenfield build — 21 commits, 183 passing tests. Your job is to
continue it, not to redesign it.

## Read these first, in this order, before writing any code

1. **`HANDOVER.md`** — what exists, how to run it, what is complete, what is
   not, and the next three actions. Start here.
2. **`spec/CORRECTIONS.md`** — read this **before** the spec. The original
   specification contains instructions that are factually wrong (a model that
   does not exist, the wrong tokenizer, an API parameter that now returns 400,
   an inverted comparison that contradicts its own test, a config that trips
   its own assertion). This file separates *wrong* from *incomplete* from
   *still valid*.
3. **`spec/BUILD-SPEC.md`** and **`spec/EXPLAINER.md`** — the original design
   documents, preserved unedited. Read for the *reasoning*: they carry the
   published evidence behind every invariant. Do not follow their commands or
   constants literally; see CORRECTIONS.
4. **`INVARIANTS.md`** — 25 invariants with the test that catches each
   violation. This is the pre-commit checklist.

Then read `src/agent/execute/loop.py`. It is the spine; everything else plugs
into it.

## What the project is

An agent that tests one question: **do written procedures make a cheap local
model competent enough to do real work, and can that be measured rather than
asserted?** Published evidence says human-written skills lift task success
~+16 points while self-generated ones average about **−1**. So the hard
problem is not generating skills — it is building an instrument honest enough
to tell a real lift from noise. Every invariant exists to protect that
measurement.

## Ground rules

- **Local only.** There is no API key. Run everything with
  `--config config.local.yaml --profile local`, which *enforces* that no cloud
  backend and no provider credential is reachable and fails loudly otherwise.
  The Anthropic adapter is written and unit-tested but has **never made a live
  call** — do not describe it as working, and do not delete it.
- **Never weaken an invariant to make something pass.** If one is in your way,
  read why it exists in the spec first. If it is genuinely wrong, say so
  explicitly and change it deliberately with a test — do not quietly route
  around it.
- **A test that a function works is not a test that the system calls it.** An
  audit here found seven components with green tests and zero call sites. When
  you implement something, add a test that the *runtime* exercises it. See
  `tests/test_wired_invariants.py`.
- **Report honestly.** If something is unverified, say unverified. If a run
  failed, show the output. The whole project is about measurement integrity;
  overclaiming in a commit message contradicts the thing being built.
- **Commit as you go**, and keep the tree clean. `git log --oneline` is the
  project's memory.

## Environment quirks that will waste your time if you skip them

- `pip install -e .` **does not work** at this path — the folder name has Greek
  characters and setuptools writes its `.pth` in the system ANSI codepage. Use
  `.\alpha.ps1`; pytest gets `pythonpath` from `pyproject.toml`.
- The virtualenv is **outside** the project at
  `%USERPROFILE%\.alpha-test-1-venv` — OneDrive deleted an in-folder one twice.
  `.\setup.ps1` rebuilds it.
- Local models need a **dedicated Ollama instance on port 11435**. Run
  `.\ollama-serve.ps1` in its own window and leave it open, or every model
  call fails with a connection refusal.
- `.cmd` files cannot carry this path. Use `.ps1`.

## Verify the system works before changing anything

```powershell
.\setup.ps1                      # expect: 183 passed
.\ollama-serve.ps1               # separate window, leave open
python demo.py                   # scripted model, no network — end-to-end
```

`demo.py` should refuse an injected instruction and report a verifying hash
chain. If it does not, stop and fix that before anything else.

## Your first task

**Settle H30b, then run the eval-split noise floor.** Details are in
`HANDOVER.md` §9, but in short:

1. `bench/tasks/aero_calc/eval/H30` expects `delta_v_capture_m_s = 2054.1`.
   An independent computation gives **2080.2** — 1.27% apart. It passes at
   tolerance 2%, but a *correct* answer scores 0.37 on the continuous scale,
   so the M7 ratchet would rank right answers as mediocre. Recompute it,
   decide which number is right, and fix either the expected value or the
   tolerance in `bench/suites/aero_calc.py`, then regenerate the suite.

2. Run the noise floor on the eval split — 15 tasks × 3 baseline runs, roughly
   7 hours, overnight-shaped:

   ```powershell
   python bench\noise.py --config config.local.yaml --profile local --split eval --runs 3
   ```

   Then classify: each task is `3/3`, `0/3`, or flaky. **If more than 8 of the
   15 are flaky, stop and tighten the task specifications** rather than
   proceeding — a lift measured against that much noise cannot be interpreted.
   Report the mean pass rate and whether it lands in the 35–65% band.

**Before spending 7 hours, do the 15-minute version first.** This pattern
caught four separate blockers in one session:

```powershell
python bench\noise.py --config config.local.yaml --profile local --runs 1 --limit 3
python bench\noise.py --config config.local.yaml --profile local --runs 1 --limit 3 --skill aero_units_discipline
```

Expected from the record in `docs/evidence/`: 0/3 baseline, 3/3 with the
skill. If you do not reproduce that, something regressed — find it before
starting the long run.

## After the noise floor

Run the hand-written calibration skill through the full gate, on the **eval
split only** (contamination rule). If the gate **rejects** it, the instrument
is broken and every later rejection is uninterpretable — fix that before
evaluating anything else. Do not accept "expect rejection, that's the gate
working" as sufficient: a gate that rejects everything and a gate that works
look identical until the calibration skill passes.

## What not to do

- Do not run the full 7-hour benchmark before the 15-minute sighting shot.
- Do not add an API key or cloud backend without being asked. The local path
  is the deliverable right now.
- Do not "clean up" the spec documents — they are a preserved record, and
  `spec/CORRECTIONS.md` is where corrections go.
- Do not run more than one uvicorn worker. The hash-chain lock is
  process-wide; a second worker can fork the chain.
- Do not loosen a benchmark tolerance to make tasks pass. That is the same
  error as writing an easy suite.

Ask before spending more than an hour of compute on anything, and before any
change that weakens a documented invariant.
