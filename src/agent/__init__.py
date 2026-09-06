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
HARNESS_VERSION = 2


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
TOOLSET_VERSION = 3
