# Invariant checklist

Print this. Check it before every commit. The parenthetical names the test
that would catch a violation, so "I checked it" means something.

 1. ☐ The stable prefix is byte-identical across turns within a task
       — `test_prefix_byte_stable_across_20_turns`, `PrefixGuard`
 2. ☐ No refinement iteration without a new external observation
       — `test_no_refinement_without_observation`
 3. ☐ Instructions found in observed content are data, never commands
       — `test_injection_blocked_end_to_end`
 4. ☐ Whoever drafts something never writes its tests
       — `test_different_author_rule`, `assert_different_authors`
 5. ☐ Held-out tests never enter any prompt
       — `test_holdout_never_enters_prompt`
 6. ☐ Monitor output never enters agent context
       — `test_monitor_never_in_agent_context`, `test_monitor_absent_from_client_stream`,
         `test_monitor_is_not_imported_by_prompt_builders`
 7. ☐ Every side-effecting tool routes through `execute_once`
       — `test_kill_and_resume`, `test_side_effecting_tools_route_through_execute_once`
 8. ☐ Trajectories are never token-pruned; only documents are compressed
       — `test_compaction_never_paraphrases_survivors`
 9. ☐ Nothing resumes without integrity verification
       — `test_anchor_mismatch_halts`
10. ☐ Only the gate promotes skills, and only with human approval
       — `test_promotion_requires_a_human`, `test_generator_cannot_promote`
11. ☐ Working context stays under the fill ratio
       — `test_ceiling_triggers_delegation_not_squeeze`
12. ☐ Every model call is recorded in `calls`
       — `test_no_call_bypasses_telemetry`
13. ☐ The planner cannot write; plan phase is read-only
       — `test_planner_cannot_write`
14. ☐ The auto-review backend differs from the executing backend
       — `test_auto_review_is_different_backend`
15. ☐ Auto-review never widens scope — it only decides, never grants
       — `test_auto_review_cannot_widen_scope`
16. ☐ TRUSTED-tier actions always park for a human, never auto-reviewed
       — `test_trusted_tier_always_parks`
17. ☐ An approved plan hash is pinned and verified before every step
       — `test_plan_hash_pinned_after_approval`

## Concurrency — the control plane serves on a threadpool

23. ☐ Appending an event is atomic (read head → write successor)
       — `test_chain_survives_concurrent_appends`
24. ☐ `execute_once` is atomic (check receipt → perform effect)
       — `test_execute_once_is_atomic_under_threads`
25. ☐ The DB connection is usable from any thread
       — `test_db_connection_is_usable_across_threads`

Both critical sections take `db.chain_lock()`. Without it the M12 endpoints
raise `ProgrammingError` on every request (thread-bound connection), and two
threads racing `append` fork the chain — which `verify()` then reports as
tampering on a database nobody tampered with.

**Do not run multiple workers against one database file.** The lock is
process-wide, so a second process would not see it. `src/agent/control/asgi.py`
says so at the top.

## Five more this build added

18. ☐ A cache hit never launders taint
       — `test_cache_does_not_launder_taint`
19. ☐ A trial task is never one the skill was drafted from
       — `test_no_contamination`
20. ☐ Untrusted content is summarized by a tool-less quarantined call
       — `test_untrusted_content_is_quarantine_summarized`,
         `test_quarantine_call_passes_no_tools`
21. ☐ Replayable code never reads the clock or generates randomness
       — `test_replayable_code_is_deterministic`, `test_prefix_builder_never_reads_the_clock`
22. ☐ Nothing UPDATEs or DELETEs from `events`
       — `test_events_table_is_append_only`

## Where the build spec and its own acceptance tests disagreed

Four places. In each, the acceptance test expressed the intent and the code
snippet did not, so the code follows the test:

* **Semantic cache taint comparison.** The snippet served an entry when
  `stored.level >= requested.level`, which hands untrusted-derived values to
  trusted callers — precisely the laundering its own test forbids. Inverted.
  (`ledger/semantic_cache.py`)
* **Self-consistency threshold.** The snippet used `agreement >= 0.67` with the
  comment "2 of 3 agree". Two of three is 0.6667, which is less than 0.67, so
  the literal constant escalates every case it was written to accept. Now a
  fraction. (`router/cascade.py`)
* **Exact-string preservation.** The path regex matched only absolute POSIX
  paths, so `src/parser.py` survived compaction as `/parser.py` — not
  character-for-character. (`ledger/compact.py`)
* **The shipped config violated invariant 14.** `auto_review_backend: "cloud"`
  with `step` absent from `local_allowlist` means the executor is also
  `cloud`, so the anti-collusion assertion fires on the first review the
  system ever performs. There is now a separate `review` backend.

## Two spec instructions that could not be followed as written

* **`muse-glimmer:3b` does not exist.** All 15 Ollama tags for Muse Glimmer are
  30B; the "3B assistant model" in the release notes is
  `Muse-Glimmer-30B-assistant`, a DFlash speculative-decoding drafter, not a
  chat model. Both tiers are Granite 4.1 instead — which satisfies §0.1's
  one-family rule better than the original pairing did.
* **The M5.3 sandbox cannot pass the M5 acceptance test as specified.** A
  subprocess with rlimits does not stop `Path(anchor_path).write_text(...)`.
  An audit hook inside the child now enforces it.
