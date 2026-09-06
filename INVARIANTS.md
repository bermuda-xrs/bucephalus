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

## Measurement runs

26. ☐ A completed benchmark run is on disk before the next one starts
       — `test_each_completed_run_is_on_disk_before_the_next_one_starts`,
         `test_a_real_kill_mid_pass_loses_at_most_one_task`
27. ☐ A resumed pass never blends two different measurements
       — `test_resume_refuses_a_ledger_from_a_different_configuration`,
         `test_resume_refuses_a_ledger_from_a_different_profile`
28. ☐ Every arm of every run starts from identical bytes on disk
       — `test_a_previous_answer_does_not_survive_into_the_next_run`,
         `test_both_arms_of_a_pair_start_from_identical_bytes`
       `verify_program` grades files, so a run that produces no answer is
       graded against the PREVIOUS run's answer. In a paired trial the two
       arms share one workspace, so the control arm could be graded against
       the answer the skill arm just wrote — manufacturing the very lift the
       gate exists to detect.
29. ☐ Trials are counted at task level, never by re-seeding one task
       — two runs of one task share its difficulty, so counting them as two
         pairs inflates n without adding information and makes the p-value
         anti-conservative. See the note in `config.local.yaml`.

`noise.py` held every result in memory and wrote once at the end, so a kill
at hour three of a four-hour pass lost all of it. Results now append to a
`.partial.jsonl` ledger with an fsync per row, and `--resume` restarts where
a killed pass stopped.

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

---

## Added 2026-09-05 (iteration one close)

**30. A machine event is never scored as a task outcome.**
A stalled or timed-out model call is aborted, the task re-runs once, and a
second stall marks it `excluded` — reported as excluded, never as a failure
and never silently dropped. `crashed` is unreachable for a machine event.
Enforced per task by `assert_machine_fault_never_scored_as_failure` in
`bench/noise.py`, before the row reaches the ledger.
*Caught by:* `tests/test_stall_injection.py`.

**31. A sandbox library may not widen the sandbox.**
Every importable library declares a pinned version and whether it touches
network, writes outside `artifact_scope`, or spawns subprocesses.
Registration refuses anything answering yes, so widening the sandbox must be
deliberate and visible in a diff.
*Caught by:* `tests/test_sandbox_libraries.py`.

**32. Runs with different toolset versions are never plotted together.**
`TOOLSET_VERSION` is separate from `HARNESS_VERSION`: the library set does not
change what counts as a pass, but it changes what a run can achieve.
*Caught by:* the refusal block in `bench/report.py`.

**33. A suite is always named explicitly.**
No tool falls back to a default suite. A missing `--suite` or `--tasks` fails
loudly naming what is absent; `ladder.py` raises on a task it has no prompt
for rather than substituting another task's.
*Caught by:* `required=True` and `SuiteDataMissing`.

**34. Resume never blends two task orders.**
`order_seed` is part of the run signature, so a ledger written under one
randomised order cannot be continued under another.
*Caught by:* `tests/test_noise_durability.py`.

## Added 2026-09-06 (iteration two, phase 1 + corrections)

**35. A backend's adapter type never decides behaviour.**
Branches ask about a declared CAPABILITY — `needs_action_envelope`,
`reports_prompt_cache`, `reaches_a_remote_provider` — never about which class
implements the backend. Two adapters can reach the same server running the
same weights, so a class-name test flips while nothing about the model
changes, and the run does not say so.
*Caught by:* `tests/test_no_adapter_identity_branches.py`, which AST-walks
`src/` and `bench/` and allows only the registry and one documented
capability table.

**36. An unmeasured quantity is null, never zero.**
`cache_hit_ratio` is `None` when no backend on the task reports cache
accounting. No alarm fires on `None`, no median includes it, and no historical
run is back-filled with a number nobody measured.
*Caught by:* `tests/test_cache_ratio_is_measured_or_null.py`.

**37. A monitored invariant must be reachable on the hardware it runs on.**
`cache_alarm` is not gated on spending money. A prefix mutation on a free
local backend costs wall clock, which is this project's binding budget; gating
the alarm on the cost axis that is structurally zero here made it dead on the
only machine it has ever run on.
*Caught by:* `test_the_alarm_is_no_longer_gated_on_spending_money`.

## Added 2026-09-06 (retrieval isolation, D17)

**38. A benchmark attempt can read only what it produced itself.**
Under `isolate_retrieval`, `search_history` and `expand_artifact` are scoped
to the attempt's task tree, so no arm of a paired trial can retrieve another
arm's answer and no rerun can retrieve the previous run's. Per ATTEMPT, not
per arm — the noise floor's 3/3, 0/3, flaky classification assumes the runs
are independent draws. Isolation with no task to scope to returns the empty
set rather than opening up.
*Caught by:* `tests/test_cross_arm_retrieval_blocked.py`, which runs the
attack across all three paths.

**39. The run record states which persistence subsystems were readable.**
A reader must never have to infer whether a paired trial could have leaked
across arms. `retrieval_isolation` is a field in every run record, and the
unisolated state says in words that it is invalid for a paired trial.

**40. The critical channel is for tampering only.**
An anchor that is merely behind its task is `info`, distinguished from a
rewrite by whether the anchored hash is still present in the chain. An alarm
with a permanent backlog is not an alarm — and memory files now sit under
this same chain.
*Caught by:* `tests/test_anchor_behind_is_not_tampering.py`.

## Added 2026-09-06 (A1 — persistent memory)

**41. Tier 1 memory is frozen at task start.**
The prefix is built from a snapshot, exactly as skills are, so a
`memory_write` during a turn cannot move the prefix. Verified two ways: it
does not trip PrefixGuard, and it does not move `cache_hit_ratio` — the
second being the server's opinion rather than this process's own, which is
what makes the first more than self-report.
*Caught by:* `tests/test_memory.py`, `tests/test_memory_end_to_end.py`.

**42. Untrusted-derived content never enters tier 1.**
`memory_write` has the same `USER` ceiling as `write_file`, so the capability
gate refuses it before the store is reached, and the store refuses it again.
Tier 2 holds it instead and returns it stamped `UNTRUSTED`. A poisoned tier 1
would be in every future prompt in every future session, including ones that
never touch the web.
*Caught by:* `tests/test_memory.py`.

**43. A change to memory is on the hash chain.**
Every write and consolidation appends an event carrying a fingerprint over
both memory FILES — not the parsed entries, because a tampered line that
fails to parse is exactly what an attacker would write.
*Caught by:* `test_a_tampered_memory_file_no_longer_matches_the_chained_fingerprint`.

**44. Tier 1 consolidates, never truncates.**
Over the cap, entries are de-duplicated first and only then evicted
oldest-first, each eviction recorded. Evicting first would discard a fact to
make room for its own duplicate.
*Caught by:* `test_duplicates_are_removed_before_anything_is_evicted`.

## Added 2026-09-07 (A2 — web tools and file ops)

**45. Web content is UNTRUSTED at every stage, with no exception.**
`web_fetch` and `web_search` stamp `UNTRUSTED` on output, which reaches both
the artifact and the taint tracker by the one shared path in `loop.py`. A
result list is a web page that happens to be shaped like data; titles and
snippets are attacker-authored on any query an attacker can influence.
*Caught by:* `tests/test_web_tools.py`, `tests/test_search_history.py`.

**46. A fetched page cannot write itself into tier 1.**
The realistic composition of A1 and A2: an agent reads a page saying
"remember X", tries to persist X, and the capability gate refuses it because
the page raised the task's taint. The content still persists in tier 2,
stamped.
*Caught by:* `test_a_fact_from_a_fetched_page_cannot_enter_tier_one`.

**47. Fetched content carries provenance produced by the fetcher.**
URL, final URL after redirects, fetch time, and a hash of the raw bytes,
recorded on the artifact. Produced where the bytes arrive rather than
reconstructed at the call site, which would record what was ASKED for — a
different thing, and wrong exactly when it matters.
*Caught by:* `test_provenance_is_recorded_on_the_artifact`.

**48. Retrieval is bounded and polite.**
robots.txt is honoured and fails OPEN when unreachable, because a site with
no robots.txt has refused nothing. A declared `crawl-delay` raises our floor
and never lowers it. `grep` and `glob` are bounded per file and overall,
because an unbounded search is the ledger discipline defeated by accident
rather than by attack.
*Caught by:* `tests/test_web_tools.py`.

## Added 2026-09-07 (B3 — tool exposure, and D18)

**49. Tool exposure is separate from tool existence, and refusals are logged.**
An agent may be denied a tool it can still see in its manual. Refusal happens
at dispatch, BEFORE the capability gate, and appends `tool.not_exposed`. The
tool stays in the manual on purpose: an agent that cannot see a tool works
around its absence silently, where a refused one leaves a record of what it
tried, and the record is the auditable behaviour.
*Caught by:* `tests/test_tool_exposure.py`.

**50. Tool policies compose by UNION of denials and can only narrow.**
A delegated child is denied everything its parent was denied plus the child
defaults. `restrict_tools` refuses a policy that would widen an existing one —
without that, an agent could delegate to itself and return with more
authority than it started with.
*Caught by:* `test_a_policy_cannot_be_widened`,
`test_restrictions_compose_by_union_of_denials`.

**51. A delegated child cannot spawn, widen its scope, or write tier-1 memory.**
Leaves cannot delegate, checked from the TASK TREE rather than a flag so it
cannot be bypassed by constructing a context differently. `artifact_scope` is
inherited and containment is by PATH, not string prefix — `work_other` is not
inside `work`. Isolation needs no separate inheritance: `visible_tasks`
scopes to the task tree and a child shares its parent's root by construction.
*Caught by:* `tests/test_tool_exposure.py`.

**52. A measurement cannot start without declaring its isolation state.**
`bench/measurement.open_measurement` is the only door, and `isolate` is
keyword-only with no default, so forgetting it is a TypeError at the call
rather than an unrestricted run found three hours later. `None` means
UNDECLARED and is not the same as `False`: false is "considered and not
needed", none is "nobody thought about it".
*Caught by:* `test_no_measurement_module_opens_a_context_for_itself`.

---

## Design rules

Not per-run invariants with a test that fires — rules for the next person
writing a control. Each is here because it has been violated more than once.

**DR1. A control's scope must match the scope of what it protects.**

Two instances, a day apart:

* **D17.** The anchor is per TASK; bench runs never checkpoint per task. So
  19 tasks drifted past their anchors doing nothing wrong, and the critical
  channel filled with 19 permanent false alarms.
* **The memory blessing.** The anchor is per CONFIG; memory is per USER. So
  running `config.local.yaml` and then `config.openai.yaml` would have had
  each flagging the other's perfectly good memory as tampered — the same
  disease, one day after curing it.

Both are the same mistake: a control stored at one granularity guarding
something that lives at another. The failure is not a missed detection, it is
a PERMANENT FALSE POSITIVE FROM ORDINARY USE, which is worse — it trains
everyone to ignore the channel, and then the real alert arrives into a list
nobody reads.

Before adding a control, ask what it protects and at what granularity that
thing lives, then store the control at exactly that granularity. If the two
cannot be made to match, the control belongs somewhere else.

**DR2. A default must not shadow the path it is a default for.**

The adjacency family's second mechanism. `AgentContext` pre-set
`web_fetcher = default_fetcher`, and `tool_web_fetch` began "if a fetcher is
configured, use it" — so the configured branch ran every time and the tool's
own path never did. Harmless while both did the same thing, and the instant
one of them gained a feature the other became unreachable code that looked
wired, with green tests throughout because both returned the same value.

A seam is for OVERRIDING. If it is pre-set to the default, it is not a seam;
it is a hard-coded path with a decoy branch beside it. Leave override seams
unset and let the consumer own its default.

