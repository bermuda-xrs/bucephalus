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
