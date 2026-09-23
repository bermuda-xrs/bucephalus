"""alpha test 1 — hybrid local/cloud agent with verified skill learning."""

__version__ = "0.1.0"

# Bump this whenever a change makes previously-recorded pass rates
# INCOMPARABLE to new ones. It is not the package version: it exists so a
# chart cannot silently mix numbers produced by different measurement rules.
#
#   1  original: verification only inside the <final> branch; grader inside
#      artifact_scope; max_steps 6; budget charged model time only.
#   2  2026-08-28: final sweep grades the artifact on every terminal path
#      (a task that did the work but never announced it now passes);
#      grader moved outside artifact_scope; max_steps 10; budget charged
#      wall clock; per-backend action protocol; monotonic scorer.
#   3  2026-09-17: `max_taint_in` is gone from the task contract, and with it
#      from every prompt prefix (D38, SPEC-DELTA C60). It changed no
#      BEHAVIOUR - dead in code since 2026-09-15, and no value but the default
#      could be set - but the contract renders into the prefix, so removing it
#      moves bytes the model reads, and this project has measured prefix
#      STRUCTURE moving pass rates (the harness tax, FINDINGS 1.6-1.8). A bump
#      for a field that does nothing is the price of not showing the model a
#      ceiling nothing enforces, which is C3's inert-guarantee class sitting
#      in the prompt itself.
#
#      Taken on D38's OWN DEADLINE, not because a bump was wanted: the entry
#      said to bump for this alone if none had come by the end of the second
#      session after 2026-09-15, and none had. Every number published under 2
#      is incomparable with every number produced from here, and nothing has
#      yet been produced under 3.
#   4  2026-09-22: THE GRADER LEFT THE PROMPT. The whole task contract was
#      rendered into the cache-stable prefix, which put `output_schema`'s
#      `x-expected` - the right answer, verbatim - and `grader_dir` - the
#      path to the grader - in front of the model on every task that had
#      them. Only an allowlisted set of contract fields is rendered now, with
#      `x-` keys stripped at every depth (`ledger/prefix.prompt_contract`).
#
#      This one is not a rule change, it is a CORRECTION: every pass rate
#      measured on a suite with answer keys was partly a memory test. The
#      2026-09-17 ablation's 0.75 across all five arms was exactly the
#      fraction of its tasks that carried a key - three of four - and the one
#      task with no key failed in every arm. Numbers under 3 and below are
#      not merely incomparable with numbers from here, they are wrong in a
#      known direction: too high.
#   5  2026-09-23: THE GRADER IS HELD OUT UNDER MEASUREMENT. v4 took the
#      answer key out of the prompt; the verifier then handed it back as
#      feedback - "$.total: expected 4825, got 0", "missing: 'INF-4471', ..."
#      - and the model copied it into its next turn. Under `open_measurement`
#      the grading verifier's result now reaches the model as pass/fail only;
#      the detail goes to `verify.*` events for the operator. And the search
#      index stops matching against the whole contract, which made
#      `search_history` an oracle on the answer key.
#
#      Outside measurement nothing changes. The defect was never that
#      feedback is detailed - it was one verifier grading the task AND
#      feeding refinement, which is the held-out/visible distinction M7.4
#      had before M11 was retired took it away (FINDINGS 1.33).
#
#      v4 ran exactly once. Its pass rates AND its token totals are void: a
#      task that copies the answer terminates on call two, so how many calls
#      an arm made says as much about the leak as about its mechanism.
HARNESS_VERSION = 5


# Bump this whenever the set of libraries model-written code may import
# changes. It is SEPARATE from HARNESS_VERSION on purpose.
#
# HARNESS_VERSION governs what counts as a PASS. The library set does not
# touch that - a correct answer is correct either way. But it absolutely
# changes what a run can ACHIEVE: a model that can import sympy is not
# attempting the same problem as one that cannot, and comparing their pass
# rates would attribute a tooling difference to the model or the skill.
#
# This exists so the next several tool additions cannot silently invalidate
# comparisons the way the aerospace suite silently became the default.
#
#   1  2026-09-05: sympy 1.13.3. Added AFTER the Phase 2A reference
#      measurement, which therefore ran at TOOLSET_VERSION 0 and is
#      reflected in no published number here.
#   2  2026-09-06: the `search_history` tool (B4). A run that can read what
#      earlier runs were asked and what they answered is not attempting the
#      same problem as one that cannot - and on a suite with repeated task
#      shapes it is the difference between solving and recalling. Nothing
#      published so far ran with it.
#   3  2026-09-06: memory_read / memory_write / memory_consolidate (A1). An
#      agent that carries facts between sessions is a different instrument
#      again. Benchmark runs disable tier 1 entirely (isolate_retrieval), so
#      a measured run has the TOOLS without the PERSISTENCE - which is
#      exactly why the version has to move: the tool manual in the prefix
#      differs from TOOLSET_VERSION 2 even when the behaviour does not.
#   4  2026-09-07: web_search, a real web_fetch, glob, grep, edit_file (A2).
#      The largest single jump so far. RETRIEVAL is the one that changes what
#      a run can achieve - until now the agent could only reason from what it
#      was given, and G4's "research-grounded skill generation" had nothing to
#      ground on. A run that can look something up is not attempting the same
#      problem as one that cannot.
#   5  2026-09-07: the `parallel` tool (A5). It adds no new CAPABILITY - every
#      tool in a batch was already callable one at a time - but it changes
#      what a second of wall clock buys, and wall clock is this project's cost
#      axis. Measurement runs pin it to width 1 and record the setting.
#   6  2026-09-15: run_code's resource limits on Windows (D37). A Job Object
#      caps memory at 512 MB per process, CPU time at the task's own timeout
#      (user-mode, every process) and processes at 32 - limits POSIX always
#      declared and Windows, the only platform any number here came from,
#      never applied. A program past them used to finish and now fails, so a
#      run can achieve less. No measurement has run under 6.
TOOLSET_VERSION = 6
