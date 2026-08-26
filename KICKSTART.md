# Kickstart prompt

Paste the block below to a coding agent working in this directory. It assumes
the agent has filesystem access here and can run commands — it does not need
files attached, only pointing at.

---

You are picking up an in-progress research project at
`C:\Users\char1\OneDrive\Desktop\Βουκεφάλας\alpha-test-1`. It is a working
system, not a greenfield build — 40+ commits, 257 passing tests. Your job is to
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
.\setup.ps1                      # expect: 257 passed
.\ollama-serve.ps1               # separate window, leave open
python demo.py                   # scripted model, no network — end-to-end
```

`demo.py` should refuse an injected instruction and report a verifying hash
chain. If it does not, stop and fix that before anything else.

## Your first task

**Drive the measurement to a decision point.** Everything is staged behind
gates that live in code, so the job is mostly to start the next step and read
what comes back.

```powershell
.\measure.ps1 status     # where things stand, and what may run next
.\measure.ps1 next       # run that step, detached, if the gates allow
.\measure.ps1 watch      # follow it
```

`bench/nextstep.py` owns the gates; `measure.ps1` only obeys them. It runs
every step detached (session teardown kills foreground jobs mid-run), refuses
to start while another measurement holds the lock, and refuses to chain past
a decision point - those are yours to make.

### The three gates, and what each protects

1. **After pass 1 of the noise floor** - is the mean pass rate inside
   25-75%? One pass of 30 is about +/-9pp at 1 SE, so this only catches
   gross miscalibration. Read the counter-argument the gate prints before
   acting on it: the noise floor measures the BASELINE arm only, and a low
   baseline with a working skill arm is near-maximum discordance, which is
   ideal for McNemar. The cheap discriminator is a skill-arm sighting shot
   on a few medium/hard tasks, not a rebalance.
2. **After all three passes** - more than 8 of 30 flaky means stop and
   tighten task specifications. A lift measured against that much noise
   cannot be interpreted.
3. **After the calibration** - if the hand-written skill is REJECTED, the
   instrument is broken and every later rejection is uninterpretable. Do not
   accept "expect rejection, that's the gate working": a gate that rejects
   everything and a gate that works look identical until this one passes.

### Before committing hours

```powershell
python bench\coverage.py --skill examples\skills\aero_units_discipline.md
```

An upper bound on discordant pairs. Only discordant pairs carry information
in McNemar's test, so a skill whose topics touch fewer tasks than
`min_discordant_pairs` cannot clear the gate however good it is. Currently
20 of 30 against a floor of 10 - reachable, not comfortable.

### The calibration runs on ALL 30, not the eval split

It is a **positive control on the instrument** - does the detector fire when
a real effect exists - not a claim that the skill generalizes. Its author saw
the whole suite, so no split is clean with respect to the author; eval-only
would buy nothing and cost half the power. The run reports PRIMARY (all 30)
and SECONDARY (the eval 15, contamination-clean but underpowered) from the
same trials, with the caveat printed beside the number.

**Generated skills stay eval-only. That split is not negotiable.**

## What not to do

- Do not start a second measurement. Two overlapping passes measure their
  tasks under 2x CPU load, and `budget_exhausted` is a TIME limit, so
  contention can manufacture that outcome. The lock enforces this now,
  but do not work around it.
- Do not run anything else on the machine during a pass. The same task
  takes 162s idle and 339-360s with a test suite alongside.
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
