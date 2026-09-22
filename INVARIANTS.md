# Invariant checklist

Print this. Check it before every commit. The parenthetical names the test
that would catch a violation, so "I checked it" means something.

 1. ☐ The stable prefix is byte-identical across turns within a task
       — `test_prefix_byte_stable_across_20_turns`, `PrefixGuard`
 2. ☐ No refinement iteration without a new external observation
       — `test_no_refinement_without_observation`
 3. ☐ Instructions found in observed content are data, never commands
       — `test_injection_blocked_end_to_end`
 4. ☐ ~~Whoever drafts something never writes its tests~~ - WITHDRAWN
       2026-09-15 with M11, the only thing it policed (FINDINGS 1.27)
 5. ☐ ~~Held-out tests never enter any prompt~~ - WITHDRAWN 2026-09-15 with
       M11: no held-out tests exist any more (FINDINGS 1.27)
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
       — `test_promotion_requires_a_human` (`test_generator_cannot_promote`
         left with the generator, 2026-09-15)
11. ☐ Working context stays under the fill ratio
       — `test_ceiling_triggers_delegation_not_squeeze`
12. ☐ Every model call made for a task is recorded in `calls` from the moment
       it starts - one that never returns keeps an open row (D36, 104-106)
       — `test_no_call_bypasses_telemetry`, `test_no_model_call_bypasses_the_record`,
         `test_a_stalled_call_keeps_its_row_and_its_time`
13. ☐ The planner cannot write; plan phase is read-only
       — `test_planner_cannot_write`
14. ☐ The auto-review backend differs from the executing backend
       — `test_auto_review_is_different_backend`
15. ☐ Auto-review never widens scope — it only decides, never grants
       — `test_auto_review_cannot_widen_scope`
16. ☐ TRUSTED-tier actions always park for a human, never auto-reviewed
       — `test_trusted_tier_always_parks`
17. ☐ An approved plan hash is pinned and verified before every step - and
       before a decided action is released on resume (Part 3)
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

## Added 2026-09-07 (D20 — verified resume)

**53. A task's execution never continues without its chain being verified.**
`run_task` calls `integrity.resume()` before marking the task RUNNING, so
every caller inherits the check — including the ones Phase 8 and Phase 11
will add. A break halts and records `integrity.halt`; an anchor merely BEHIND
proceeds, because a task that never checkpointed is not a tampered one.
*Caught by:* `tests/test_resume_is_verified.py`, which injects all three
conditions rather than reasoning about them.

**55. Authorship is read from the chain, not from a deduplicated row.**
`expand_artifact` decides scope from `artifact.written` events, which are
appended per task. The `artifacts` row is content-addressed and keeps only
the first writer, so it answers a different question.
*Caught by:*
`test_an_arm_can_expand_an_artifact_whose_bytes_another_arm_wrote_first`.

**54. One key in the anchor means one kind of record.**
Chain heads are written under the task id; plan pins under `plan:<task id>`.
Sharing the keyspace meant the last writer won, which made the resume check
call a plan pin a rewrite AND made the plan-pin check silently skip whenever
a checkpoint followed it.
*Caught by:* `test_a_plan_pin_does_not_read_as_a_rewritten_chain`,
`test_the_plan_pin_check_survives_a_later_checkpoint`.

## Added 2026-09-07 (A5 — parallel tool execution)

**56. Only read-only tools run in a batch, and never a model call.**
`parallel` refuses any side-effecting tool for the whole batch — idempotency
receipts assume ordered execution, and two writes racing for one receipt key
is a corrupt state rather than a slow one. No batchable tool calls the model:
one Ollama instance on CPU, where concurrent generation contends for the same
cores and corrupts the only cost axis this project has.
*Caught by:* `tests/test_parallel_tools.py`.

**57. Every call in a batch is gated before any call runs.**
Exposure and the capability gate are per call, and concurrency is a new way
for a check to be skipped for one call while passing for another. One refusal
means nothing executes: a batch that partially applied would be worse than
one that failed, because the model would see some results and no clear signal
that the rest were refused. The refusal is recorded, not only returned.
*Caught by:* `test_a_denied_call_refuses_the_whole_batch_and_nothing_runs`,
verified by injecting interleaved execution.

**58. A measurement run executes tools sequentially, and records that it did.**
`max_parallel_tools` is pinned to 1 by `open_measurement` and reported in the
run record. A task that read five files at once is not comparable with one
that read them in turn, and wall clock is the cost axis.

## Added 2026-09-07 (B5 + A8 — streaming and the terminal surface)

**59. The banner is display-only and never reaches a prompt.**
Loaded by `tui/theme.py`, which no prompt-building module imports. Enforced
two ways: a structural check that the prompt path does not import the surface
layer, and a test that greps EVERY prompt sent during a real task for the
banner's own lines. Verified by injecting the banner into `PrefixBuilder`
and confirming the test fails.
*Caught by:* `tests/test_tui.py`.

**60. Integrity alerts never share a collection or a colour with approvals.**
Two separate lists on `RunState`, not one list with a severity field, so they
cannot be accidentally rendered alike. `danger` is reserved and a test greps
the surface for any other use of it. The alert COUNT sits on the
always-visible budget bar, not only in a tab someone has to think to open.
*Caught by:* `tests/test_tui.py`.

**61. A display can never fail a task.**
The stream bus drops a listener that raises rather than propagating it, and
bus events are published outside the adapters' error handling. A broken
surface is a broken surface, not a failed run.
*Caught by:* `test_a_listener_that_raises_cannot_fail_the_task`.

**62. The live bus is a view, never the record.**
Nothing published on it enters the hash chain, and nothing on it may be read
back to claim what happened. Bounded twice — per task, and in how many tasks
are retained — because a buffer of model output keyed by task id with no
ceiling is a leak that only shows after a long day.
*Caught by:* `test_the_bus_is_not_the_record`.

## Added 2026-09-08 (A4 — the message channel)

**63. A task's origin is on its ROW, and its restrictions are derived from
that row at check time.**
Never installed into process memory at creation. `_tool_policy` is a dict in
one process; a task row is durable and a parked task spends most of its life
waiting for a human. Deriving the floor on read also makes it unbypassable
rather than merely un-widened — a caller who never calls `restrict_tools` at
all still gets the floor.
*Caught by:* `test_the_restriction_survives_the_process_that_created_it`.

**64. Every inbound channel message is UNTRUSTED, and the taint is seeded
where the task runs, not where it arrived.**
`ctx.taint_tracker` observes the goal of a channel task as untrusted content
the first time anyone asks for the tracker. The bot is one caller; this is the
choke point every execution path goes through. The cost is real and intended:
a channel task cannot write a file whose content is quoted from the request.
*Caught by:* `test_the_message_is_untrusted_not_user`.

**65. A channel task never auto-proceeds. BOTH paths, not just the deputy.**
`auto_review` is skipped and logged as `review.skipped`; the
`approve_scoped_writes` fast path in the capability gate is also refused. The
second is the easier one to miss because it is a config flag rather than a
model call.
*Caught by:* `test_the_deputy_never_sees_a_channel_task`,
`test_approve_scoped_writes_does_not_apply_to_a_channel_task`.

**66. A message from a sender who is not on the allowlist creates nothing.**
No task, no approval, no event. A row in `channel_drops` holding a hash and a
length — never the body, because `events` feeds the FTS index and a dropped
message stored there would be a stranger writing into the corpus the agent
can search. Bounded, because unauthenticated senders write those rows.
*Caught by:* `test_an_unknown_sender_is_dropped_and_leaves_nothing_to_approve`,
`test_the_dropped_message_body_is_nowhere_in_the_database`.

**67. An approval decision is final.**
`resolve_approval` refuses a second decision instead of applying it. A console
approval vanishes from a live list once decided; an inline button stays in the
chat forever and stays tappable. Without the guard the chain would have held
two validly-appended, correctly-hashed, contradictory human decisions.
*Caught by:* `test_a_stale_button_cannot_re_decide`.

**68. The sender is re-verified at DECISION time, not only at receipt.**
The button outlives the allowlist. Checking only on arrival makes a removal
take effect for new requests and not for pending ones, which is the wrong way
round: the pending ones are the ones with an action attached.
*Caught by:* `test_the_sender_is_re_verified_at_approval_time`.

**69. Where to send a task's approval back to is durable too.**
`channel_routes`, not a dict on the bot, and `resend_pending()` re-asks at
startup. The memory version fails closed rather than open, and still fails:
the human is never asked. Local approvals are skipped — a local task parks the
same way and must not be pushed to a phone.
*Caught by:* `test_a_task_parked_before_a_restart_is_asked_about_again`.

**70. A channel adds no listening surface.**
Long polling, not a webhook. Nothing in `agent/channel/` binds, listens, or
imports the control plane, and `ALPHA_ALLOW_REMOTE` is untouched.
*Caught by:* `test_the_channel_binds_nothing`,
`test_allow_remote_is_untouched_by_this_phase`.

## Added 2026-09-13 (the registers, D25, and B1 - sessions)

**71. A human decision has an effect.**
A decided approval is released when its task resumes, before the model's first
turn: executed through the ordinary receipt path (keyed on the parked step, so
at most once), or handed back as a denial. Exposure and the gate's hard denials
are re-checked first - a policy that narrowed while the task was parked voids
the approval rather than being overridden by it.
*Caught by:* `tests/test_decisions_take_effect.py`.

**72. A task's taint state is rebuilt from the record, per write.**
From its own `artifact.written` events, which carry that write's taint - not
from artifact rows, which keep only the first writer's.
*Caught by:* `tests/test_lifetime.py`.

**73. A paired trial's arm assignment is durable, the empty set included.**
A resumed attempt whose recorded skills will not reload byte-for-byte fails
rather than running under a different set.
*Caught by:* `tests/test_lifetime.py`.

**74. A channel update is claimed at most once, and the poll offset survives
a restart.**
*Caught by:* `tests/test_lifetime.py`.

**75. The agent's own state is not a file any task may read or write.**
Protected by CONFIGURED path, not conventional name: the database and every
SQLite sidecar, the anchor, the skills store, tier-1 memory. An empty or
root-wide artifact_scope does not reach them.
*Caught by:* `tests/test_storage_layer.py`.

**76. A database crosses the sandbox whole.**
Its `-wal` and `-journal` travel with it, and are mirrored exactly on copy-back
so a stale `-wal` cannot be replayed onto newer pages.
*Caught by:* `tests/test_storage_layer.py`.

**77. Evidence is copied through the backup API, never over earlier evidence.**
*Caught by:* `tests/test_storage_layer.py`.

**78. The mock's prefix cache is keyed on the prefix bytes.**
It is the stand-in for the server whose cache ratio is the independent opinion
of prefix stability; a mock that caches whatever arrives has no opinion.
*Caught by:* `test_the_mock_cache_has_an_opinion_about_the_bytes`, and
`test_cache_hit_ratio_above_threshold` is now red under a per-call mutation.

**79. The Anthropic adapter streams, on one path.**
*Caught by:* `tests/test_anthropic_streaming.py` - against a fake client. The
live path is D26.

**80. A session belongs to exactly one task, and a task has at most one open
session.**
Both in the schema: a trigger refuses a parent from another task, a partial
unique index refuses a second open session. What a boundary seeds a child with
is derived content, and derived content inherits its attempt's scope.
*Caught by:* `tests/test_sessions.py`.

**81. The pre-compaction flush is one chance, and its outcome is never
silent.**
A refusal writes `memory.flush_refused` and tells the model in the child's
tail. A flush that would need a human is refused, never parked. Where tier 1
cannot be written it is skipped, and the boundary's event says why.
*Caught by:* `tests/test_sessions.py`.

**83. Protected state is unreachable by FILE path from any scope.**
Every config's databases and sidecars, anchors, skills, memory and its
blessing, graders, the harness, run records, published evidence, secrets and
configs, and the task's own grader. The gate checks EFFECTIVE arguments
(defaults included), refuses a path that is, lies inside, or contains protected
state, refuses file-globs with `..`, and refuses `run_code` whose scope would
link state into its sandbox; the sandbox refuses protected reads, sqlite
connections, listings and process starts. Not an adversary boundary - D32.
*Caught by:* `tests/test_state_unreachable.py`.

**82. A session's tier-1 snapshot lives on its row.**
A boundary is the only thing that replaces it, and the only place a deferred
memory write becomes visible.
*Caught by:* `test_a_resumed_task_keeps_its_sessions_memory_snapshot`.

## Added 2026-09-13 (A3 - cron)

**84. A schedule is a fact; its next fire is derived and stored nowhere.**
Every wake computes the first occurrence of the spec after the latest one
recorded in `schedule_firings`. No column holds a computed next-fire time.
*Caught by:* `test_a_restart_neither_double_fires_nor_drops_the_next`,
`test_the_next_fire_is_stored_nowhere`.

**85. An occurrence is claimed before it acts, and only once.**
A primary key on (schedule, occurrence) refuses a second claim, so two
supervisors or one restarted mid-wake cannot both fire it. At-most-once: a
claimed occurrence whose task was created before a crash is run by the next
wake, because what to run is derived from rows.
*Caught by:* `test_an_occurrence_can_be_claimed_only_once`.

**86. A missed window is logged once and never replayed.**
Occurrences that passed while nothing watched become ONE missed row per wake,
with the span and a count. Only an occurrence within the grace fires; a run
still pending or parked is not joined by a second copy.
*Caught by:* `test_a_dark_window_is_logged_once_and_not_replayed`,
`test_an_occurrence_older_than_the_grace_is_missed_not_fired`,
`test_a_parked_run_is_not_joined_by_a_copy_every_minute`.

**87. An unattended task never auto-proceeds - channel or schedule.**
No deputy, no scoped-write fast path. A scheduled task at a boundary parks,
raises a notification, and is resumed by the next wake once a human decides.
*Caught by:* `test_a_boundary_parks_notifies_and_the_approval_is_actionable`,
`test_the_deputy_never_sees_a_scheduled_task`.

**88. A scheduled task's tool floor is derived from its origin, and every run
record carries its origin.**
*Caught by:* `test_the_scheduled_origin_derives_a_tool_floor`,
`test_scheduled_runs_are_separable_in_telemetry`.

## Added 2026-09-13 (consolidation - empty means nothing, and the measurement signal)

**89. An empty scope permits no path.** A task that declared no
`artifact_scope` may pass no path-shaped argument to any tool (D29).
*Caught by:* `test_a_task_that_declared_no_scope_may_touch_no_path` (six routes).

**90. Code reads only what the task declared, plus the interpreter.** In
agent-code mode the sandbox refuses opens and listings outside the work
directory, the linked scope, and the interpreter's own paths, captured before
any user code runs. Advisory, like everything in that hook (D32).
*Caught by:* `test_code_cannot_read_what_the_task_did_not_declare`,
`test_code_still_reads_its_scope_and_imports`.

**91. The trusted tier is human-only in code, at both layers.** Neither
`_adjudicate` nor the deputy's own check depends on `human_only_tiers` for it.
*Caught by:* `test_the_trusted_tier_is_human_only_whatever_the_config`,
`test_the_reviewer_itself_escalates_the_trusted_tier`.

**92. An unknown drafting set certifies no trial.** No skill row, or a row with
no `drafted_from`, refuses the trial; `[]` is a declaration and is honoured.
A drafting set is declared once and never overwritten, and the trials already
recorded when it was declared are counted beside it.
*Caught by:* `test_an_unknown_drafting_set_certifies_no_trial`,
`test_an_unknown_skill_certifies_no_trial`, `test_a_declared_drafting_set_is_honoured`,
`test_a_drafting_set_is_declared_once_and_never_rewritten`,
`test_a_declaration_after_trials_says_so`.

**93. A parent that declared no scope can give a child none.**
*Caught by:* `test_a_parent_that_declared_no_scope_can_give_a_child_none`.

**94. A loopback-only control plane refuses a request with no Host header.**
*Caught by:* `test_a_request_with_no_host_header_is_refused`.

**95. Every tool parameter is classified path or not-path before it ships.**
The path checks are keyed on argument names, so an unclassified name would be
unchecked. *Caught by:* `test_every_tool_argument_is_classified`.

**96. The measurement lock publishes its holder, and nothing scheduled fires or
runs while a live holder exists.** One lock per project, whatever a run's
`--out` says; the holder is readable without acquiring anything; a dead holder
reads as stale by PID.
*Caught by:* `test_the_holder_is_readable_without_taking_the_lock`,
`test_a_dead_holder_reads_as_stale_not_held`,
`test_measurements_share_one_lock_whatever_their_output`,
`test_the_supervisor_will_not_fire_during_a_measurement`,
`test_a_fired_task_waits_for_the_measurement_to_end`.

## Added 2026-09-14 (consolidation - code that has never run for real)

**97. What `run_code` ran is recoverable from the record, byte for byte.** The
full source is stored content-addressed and its SHA-256 rides the chained
`tool.intent`. *Caught by:* `test_the_full_source_is_recoverable_from_the_record`.

**98. What agent code touched is on the chained receipt, and the model's view
is unchanged.** Every open, listing, database and filesystem change outside
the interpreter's own paths, refusals included. Unmeasured is null.
*Caught by:* `test_what_the_code_touched_is_on_the_receipt`,
`test_a_refused_read_is_recorded_as_refused`, `test_imports_do_not_flood_the_record`,
`test_the_observation_the_model_sees_is_unchanged`,
`test_touched_is_null_when_nothing_captured_it`.

**99. A database is fenced by what SQLite will open.** For agent code: a URI is
read as SQLite reads it, every file SQLite opens must be inside the task's
scope, ATTACH is authorized - and refused when its target is not yet known -
VACUUM INTO likewise, and native extensions do not load. Advisory, like the
rest of the hook (D32).
*Caught by:* `tests/test_sqlite_routes.py`.

**100. ~~`generate_skill` refuses until M7.4 rules 2 and 3 run.~~** WITHDRAWN
2026-09-15: the generator itself is retired, with both rules (FINDINGS 1.27).

## Added 2026-09-14 (consolidation - real runs)

**101. A resume check that passes says what it verified.** `integrity.verified`
carries the chain head and the anchor state compared - `none`, `matched` or
`behind` - on the chain it checked.
*Caught by:* `test_a_resumed_run_records_the_anchor_it_was_checked_against`.

**102. A plan-pin check that passes says what it compared.** `plan.pin_verified`
carries the plan hash and `matched`, or `absent` when the anchor half had
nothing to compare against.
*Caught by:* `test_every_step_records_the_pinned_plan_it_verified`,
`test_a_pin_with_nothing_anchored_says_so`.

**103. Nothing a resumed run does precedes the pin check - a released decision
included.** *Caught by:*
`test_a_plan_changed_while_parked_halts_before_the_decided_action_runs`.

## Added 2026-09-15 (consolidation - the decisions)

**104. Every model call made for a task goes through `AgentContext.call_model`.**
A `.complete(` anywhere else in `src/agent` fails the suite. The two throughput
probes, which have no task, are the named exceptions. Five call sites - the
deputy, the quarantined summarizer, the monitor, the self-consistency verifier,
the generator - never wrote a row before this.
*Caught by:* `test_no_model_call_bypasses_the_record`, `test_the_deputy_is_on_the_record`.

**105. A call's row exists before the call returns.** It is written with null
usage and completed on return. An abort records its elapsed time and why:
`stalled`, `error` or `interrupted`. A row with no outcome is a call that never
ended, and `metrics` counts it as `n_unreturned`. A pre-D36 `calls` table is
rebuilt in one transaction with every row kept and marked `returned`.
*Caught by:* `test_a_call_is_on_the_record_before_it_returns`,
`test_a_stalled_call_keeps_its_row_and_its_time`,
`test_a_call_that_never_returns_stays_open_and_is_counted`,
`test_the_calls_table_migrates_without_losing_a_row`.

**106. The router estimates from returned calls only.** An unreturned call's
usage is unknown, not zero, and a stall is a machine fault - the rule the
reference measurement already excludes it by. It stays on the record and out
of the estimate. *Caught by:* `test_router_inputs_ignore_calls_that_never_returned`.

**107. A contamination caveat is read from the skill's provenance, never
assumed.** It names who drafted the skill, how many scored tasks are in its
declared drafting set (CONTAMINATED if any), and its declared exposure. A
missing record or an undeclared set says so and treats every task as
contaminated. *Caught by:* `test_the_caveat_is_derived_from_what_the_skill_declares`,
`test_the_whole_calibration_path_reports_two_numbers`.

## Added 2026-09-15 (consolidation - the second decisions)

**108. A measurement entry point reaches its first decision, and releases its
lock on every exit - early returns and exceptions included.** `noise.py main`
read `ctx` before assigning it from 2026-09-06, so no noise floor could start;
and both it and `cmd_paired` held the lock past any exit but success.
*Caught by:* `test_main_reaches_its_first_decision_and_releases_the_lock`,
`test_an_exception_in_main_releases_the_lock`,
`test_an_exception_mid_run_releases_the_measurement_lock`.

**109. ~~`max_taint_in` is dead in code and alive in bytes.~~ SUPERSEDED
2026-09-17 by 118: the bump came and the field is gone from both.**
*Caught by:* `tests/test_max_taint_in_is_dead.py`.

**110. A promotion states why the gaming veto does not apply.** Every check is
held out and the grader is unreachable (SPEC-DELTA C1); the verdict says so in
words, never a bare "skipped".
*Caught by:* `test_a_promotion_says_the_gaming_veto_does_not_apply_and_why`,
`test_a_promotion_states_why_the_gaming_veto_does_not_apply`.

## Added 2026-09-15 (steps 4-5 - resume history, and the Windows limits)

**111. A resumed task is shown what it saw when it parked, and where the rest
is.** The tail is written at the park as structured entries, each with its own
taint, and restored entry by entry, followed by the id of every earlier
observation. It is verified against the hash its chained `task.park_seed`
committed to, and a mismatch halts. The step counter is not restored.
*Caught by:* `tests/test_park_seed.py`.

**112. On Windows, sandboxed code runs inside a Job Object.** The kernel
enforces memory, CPU time and process count. The interpreter the venv
launcher starts is born inside the job, because the launcher is started
suspended. A job that cannot be applied refuses the run. File size is not
limited on Windows (Phase 6).
*Caught by:* `tests/test_sandbox_limits.py`.

## Added 2026-09-16 (the entry-point sweep)

**113. Every documented entry point is invoked by a test, not only imported.**
`demo.py` is run end to end and its summary asserted; `bench/noise.py main`
runs to its first decision; the CLI and every `bench/*.py main` start and build
their parsers. Two are exempt and named in the test: `cache_reuse_probe.py`
(no parser, needs a live server) and the PowerShell wrappers.
*Caught by:* `tests/test_entry_points.py`, `tests/test_noise_main.py`.

---

## Added 2026-09-17 (the handover rewrite)

**114. The handover names nothing that is not there, and states no number that
rots.** Every path, quickstart command, subcommand and document section it
points at is checked against the repository; the commit it was verified at must
be an ancestor of HEAD; the versions it states must match `agent/__init__.py`.
Counts that change weekly - tests, open defects - are NOT written down: section
D gives the command that regenerates each instead, and the test fails if a count
appears in prose. Stale documentation is a defect class here with four instances
(FINDINGS 1.28), and a document nothing checks is the fifth waiting.
*Caught by:* `tests/test_handover_is_current.py`.

---

## Added 2026-09-17 (the CLI sweep, D24, and G1's ablation arms)

**115. Every CLI subcommand is entered by a test, in a subprocess, against a
scratch installation.** Not `--help`, which builds the parser and stops at the
door - the command runs, its exit code is asserted, and one fact about what it
changed is checked. `ALPHA_DATA_DIR` redirects the database, anchor, skills,
`runs/`, `work/` and the operator's persistent memory into one temporary
directory, so no test can write into real state. A subcommand added without a
test fails the sweep rather than waiting for someone's hand-run hour (DR5).
*Caught by:* `tests/test_cli_subcommands.py`.

**116. A plan row is keyed by what it belongs to, not only by what it
contains.** `plans` is keyed `(id, task_id)`. The id is the hash of the plan
BODY, which carries no task id, so two tasks planning the same work produce the
same id - the ordinary case in a paired trial, where both arms plan the same
task. Under the old `id`-only key the writers' `INSERT OR REPLACE` made the
second task's draft silently delete the first task's approval (D24, C68).
*Caught by:* `tests/test_deferred_fixes.py`.

**117. An ablation flag removes the MECHANISM, never merely its
enforcement, and the arm travels in the run record.** Turning off compaction
stops both `compact()` call sites; turning off the semantic cache stops the
write as well as the read; turning off prefix stability replaces the
task-start snapshots with live reads rather than only disarming `PrefixGuard`,
which would change no prompt byte; turning off code-as-action removes
`run_code` from the registry, the schemas, the tool manual, the sandbox-library
note and the system contract. `mechanisms` and `ablated` land in
`profile_provenance`, which every run record carries, so an ablated number
cannot arrive labelled like a control one - the same argument that makes the
profile declared and enforced rather than inferred.
*Caught by:* `tests/test_g1_ablation.py`.

**118. `max_taint_in` exists nowhere - not in the contract, not in the prefix,
not as a labelled legacy constant.** It was dead in code from 2026-09-15 and
its bytes left with HARNESS_VERSION 3 on 2026-09-17, taken on D38's own
deadline rather than because a bump was wanted. A contract stored before the
bump still LOADS - pydantic ignores the unknown key - because a bump makes old
numbers incomparable, which is its purpose, and must not make old rows
unreadable, which would be data loss dressed as versioning.
*Caught by:* `tests/test_max_taint_in_is_dead.py`.

---

## Added 2026-09-22 (the grader leak)

**119. Nothing the grader uses reaches the model's prompt, and the rule is an
ALLOWLIST.** Only `goal`, `output_schema`, `success_criterion` and
`artifact_scope` are rendered from the task contract, with `x-` keys stripped
at every depth; `grader_dir` and anything unclassified are dropped. A field
added to `TaskContract` fails the suite until it is classified as visible or
hidden with a reason. A denylist would have to predict the next grading key's
name - this one was not called `verify.py`, and the next will not be called
`x-expected`. The verifier reads the key off the contract object, which is
untouched.
*Caught by:* `tests/test_grader_is_not_in_the_prompt.py`.

**120. No task content, and no grading key, reaches the installed package.**
`bench/` is the developer benchmark; `--suite` is required with no default, so
a user's numbers are always about a suite they wrote. The exclusion is
structural - `bench/` sits outside `src/` - which is not the same as decided,
so a real wheel is built and inspected.
*Caught by:* `tests/test_the_wheel_ships_no_tasks.py`.

---

## Design rules

Not per-run invariants with a test that fires — rules for the next person
writing a control. Each is here because it has been violated more than once.

**DR1. A control's scope must match the scope of what it protects - in WIDTH
and in LIFETIME.**

Two axes, one question: what does this control protect, and does it live where
that thing lives? Asked about space, it finds a key too wide. Asked about time,
it finds a holder too short-lived.

### Axis 1 - WIDTH: a key shared with something else


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

* **The anchor keyspace (D21).** Chain checkpoints and plan pins were both
  written under the task id, so the last writer won — and the two readers
  failed in OPPOSITE directions, one crying tampering and one silently
  skipping itself. A namespace collision rather than a granularity mismatch,
  with the identical consequence: a control reporting on something other than
  what it guards.

* **Content-addressed rows (D23).** `artifacts.id` is a hash of the content
  and rows are deduplicated with `INSERT OR IGNORE`, so the `task_id` column
  records only the FIRST writer. A scope check reading it asked "who owns the
  row" when it meant "who produced this" — and both arms of a paired trial
  produce identical `list_dir` output, so the second arm was refused its own
  artifact.

**The sweep, 2026-09-07.** Every shared keyspace, reported even where clean:

| keyspace | key shape | collides? |
|---|---|---|
| anchor | task id, and `plan:<task id>` | **was** — D21, fixed |
| `artifacts.id` | `sha256(content)[:32]` | **was** — D23, fixed |
| `plans.id` | hash of the plan body | **yes, loudly** — D24, logged |
| `receipts.idempotency_key` | includes the task id | clean |
| `semantic_cache.key` | `hash(op, input)`, taint-checked on read | clean |
| `search_index` | derived, rebuilt from the log | clean |
| memory blessing | one file per user store | clean |
| `skills UNIQUE(name, version)` | intended dedup | clean by design |

Before adding a control, ask what it protects and at what granularity that
thing lives, then store the control at exactly that granularity, under a key
nothing else writes. Where a key is derived from CONTENT, ask separately who
produced it — content addressing deduplicates rows and therefore discards
every writer after the first. If the two cannot be made to match, the control belongs
somewhere else.

### Axis 2 - LIFETIME: a holder that dies before the thing it holds state about

Found by A4, named 2026-09-13. Restricting a channel task with
`ctx.restrict_tools()` at creation is right in scope and wrong in duration: the
control lived in one process and the task row outlives every process that
touches it. The damage has the same shape as axis 1's - not a crash, a quiet
wrong answer - but it arrives on RESTART, which is exactly when nobody is
watching: a parked task waiting for a human spends most of its life there.

**The transferable rule: store the FACT, derive the RULE.** Facts do not go
stale. A stored rule freezes each row under whatever policy existed when it was
written, and the rows most likely to be sitting parked are the oldest.

**The sweep, 2026-09-13.** Every in-memory structure holding state about
something durable, reported even where clean:

| holder | state about | what a restart did | now |
|---|---|---|---|
| `_tool_policy` | a channel task's restrictions | parked channel task came back unrestricted | floor derived from `tasks.origin` (Phase 8) |
| bot chat binding | who to ask | approval requests never delivered | `channel_routes` (Phase 8) |
| `_taint` | what a task observed as untrusted | empty tracker; latent only because a resumed task also forgot its history - two gaps cancelling, which Phase 9 would have ended | rebuilt from `artifact.written` events, which now carry each write's own taint |
| `_frozen_skills` | the paired-trial ARM ASSIGNMENT | a resumed control arm retrieved promoted skills; a skill arm testing a candidate lost it | `skills.frozen` event, empty set included; a set that will not reload byte-for-byte fails the task |
| bot poll offset | which messages became tasks | crash mid-run: the message redelivered, a second task | `channel_updates`, claimed at-most-once |
| `_gate_queue` | candidates awaiting the gate | forgotten - and read by nothing in production | derived from `status='candidate'` |
| `_memory_frozen` | a task's tier-1 snapshot | deferred memory writes visible early | on the session row (Phase 9) |

Clean, with the reason:

| holder | why it is fine |
|---|---|
| `_origin` | a cache of a column that is never updated |
| control plane `_runs` | thread handles; a thread cannot outlive its process, so the scopes match |
| `stream._listeners`, `_backlog` | a view by design (invariant 62), never read back as a record |
| `net/politeness` state | courtesy state about outside hosts; a restart resets a delay, not a fact |
| `ToolRegistry`, sandbox libraries | rebuilt identically from code in every process |
| `PrefixGuard` | per session by design; Phase 9 resets it at a boundary and nowhere else |
| TUI `RunState` | a view |

**The limiting case is not in the table, because it held nothing in memory.**
Approvals were stored durably, finalised, anchored - and nothing derived any
rule from them at all. No process ever read `status='approved'`, so approving
an action never made it happen (C43). The fact was stored; the rule was never
written.

**Count, 2026-09-13: eleven instances** - four on width (D17, the memory
blessing, D21, D23), seven on lifetime (the table above). The one worth
remembering is the arm assignment: the MEASUREMENT APPARATUS ITSELF held durable
state in process memory. A paired trial's validity rests on which arm got which
skills, and that fact lived exactly as long as the process running the trial.

Before adding a control, ask what it protects, at what granularity that thing
lives, and FOR HOW LONG. Store the control at that granularity, under a key
nothing else writes, somewhere that lives at least as long as what it guards.

**DR3. A control must guard the BYTES, not one route to them.**

The adjacency family's fourth mechanism. Retrieval isolation guarded the query
path and the file path read the same database. Argument scoping guarded the
arguments a caller passed and not the ones the tool defaulted to. The sandbox
guarded writes and not reads. C41's test scanned the main database file while
the rows sat in its `-wal`. Each control was correct on its route and each had
a sibling route nobody had listed.

Before calling something protected, enumerate EVERY route to its bytes - query,
file read, traversal from above, pattern expansion, code execution, a copy - and
test each one, not the route the control was written for (C50).

**Instance, 2026-09-14 (C55).** The sandbox refused `sqlite3.connect` of a
protected path and opened the same database for `file:<path>?mode=ro`: it
checked the URI as a path under the work directory, and SQLite opens files
itself, in C, where no Python audit event sees them. ATTACH and VACUUM INTO
open files from inside SQL with no event at all. One spelling of the route,
guarded; five others, not.

**DR4. Empty means nothing.**

The adjacency family's fifth mechanism, and the only one where nothing is
adjacent: the control exists, is consulted, is aimed at the right thing - and
because its list is empty or its input missing, it permits everything. It reads
like a configuration question and is a DEFAULT question: what does the control
do when nobody filled it in? The failure is silent in the permissive direction,
and nobody fills in a list that already works.

Phase 8 settled it for one control (`allowed_senders`: empty means NOBODY).
D29 found the same shape in `artifact_scope`, and the sweep found six more.

**The sweep, 2026-09-13.** Every allowlist, denylist, scope list, and policy
set, reported even where correct:

| control | kind | empty / absent meant | safe? | now |
|---|---|---|---|---|
| `artifact_scope` (`within_scope`) | scope list | every path | **no** - D29 | no path; `alpha new` and `POST /tasks` warn at creation |
| sandbox reads | denylist over allow-all | every file not on the protected list | **no** | fenced to workdir, linked scope, and the interpreter's own paths |
| `scope_widens(parent=[])` | scope list | nothing widens, so a parent with no scope could hand a child any | **no** | every child entry widens |
| `human_only_tiers` | policy set | `[]` sent a trusted-tier call to the deputy | **no** | trusted tier human-only in code, at both layers |
| `check_contamination` | derived set | no row, or no `drafted_from`: every trial certified clean | **no** | refused; `[]` is a declaration and is honoured |
| control-plane Host check | input | an absent header skipped the rebinding check | **no** | refused unless `ALPHA_ALLOW_REMOTE` |
| path-argument checks | key list | a parameter not NAMED like a path is never checked | **no** - latent: every path parameter today is `path` | every parameter of every tool must be classified |
| `allowed_senders` | allowlist | nobody | yes | Phase 8 |
| `ALLOWED_SCHEMES` (web_fetch) | allowlist | constant; an unlisted scheme is refused | yes | |
| web_fetch private / loopback / link-local | denylist | refused unless `allow_private` (default off) | yes | |
| `EVENT_TYPES` | allowlist | an unlisted type is refused at append | yes | |
| `local_allowlist`, `laptop_allowlist` | allowlist | nothing routes local | yes | |
| `load_policy` fallback | policy | "NO POLICY FOUND. Deny every action" | yes | |
| `approve_scoped_writes` | policy | needs a non-empty scope; unattended origins excluded | yes | |
| `reset_workspace` | scope list | clears nothing | yes | |
| sandbox writes | allowlist | workdir only | yes | |
| search `allowed_tasks` | allowlist | no results | yes | |
| `visible_tasks(None)` | allowlist | the empty set | yes | |
| `CLIENT_EVENTS`, `OPEN_PATHS` | allowlist | nothing | yes | |
| verifier grader lookup | scope | `artifact_scope or ["."]`, harness side; a missing grader fails the task | yes | |
| `ToolPolicy` for a local task | denylist | nothing denied | yes, by design: attended; the channel and schedule floors are constants | |
| `isolate_retrieval` | policy | unrestricted retrieval | yes, by design outside measurement; `bench/measurement.py` pins it | |
| `PROTECTED_*`, `PROJECT_STATE` | denylist | constants; a config's own state always included | yes as a denylist - which is why reads needed the fence above | |
| `max_taint_in` | ceiling | never enforced - inert rather than empty | -> Part 2; dead in code 2026-09-15, bytes wait for the next bump (D38, 109) | |

Before adding a control, write down what it does when its list is empty and
when its input is missing. If the answer is "permits", the control is a
suggestion. Empty and absent are inputs like any other; they resolve to nothing
permitted, and the refusal says what to declare.

**Instance, 2026-09-14.** SQLite asks its authorizer about an ATTACH before a
bound parameter or an expression has named the file, so the target arrives as
`None` - and the first fence read `None` as nothing to check. An unknown
target is refused.

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


---

**DR5. A defect is never in one place. The sweep is part of the fix, not a
follow-up.**

Every defect this project has fixed had siblings, and every sweep found them:

| defect | siblings the sweep found |
|---|---|
| C58 - an unmeasured value formatted with `:.0%` | the CLI was fixed 2026-09-14 and **nobody swept**. `demo.py` carried the same line and died for ten days (C67, FINDINGS 1.28) |
| C34 - a pre-set seam made the tool's own path dead | `ctx.web_fetcher`, and the whole class of pre-set overrides (DR2) |
| C41 - a check blind to the WAL sidecar | every file-copy path that carries a database |
| C50 / C55 - one route into protected state | six file routes, then every SQLite route |
| DR1 - a control scoped narrower than what it protects | eleven instances in one sweep |
| C64 - one commit, two dead entry points | `noise.py main` found first, `demo.py` only because the sweep looked |

**The rule:** when a fix lands, grep for the shape, not the symptom - the
format spec, the call site, the field name - and fix every instance in the
same change. A fix that closes one instance and schedules the sweep has not
been finished; it has been split, and the second half does not happen.

The cheapest sweep is a test that cannot pass while a sibling exists: a
structural scan for the shape, like `test_no_model_call_bypasses_the_record`
or `tests/test_entry_points.py`.

---

**DR6. An entry is closed in the change that fixes it, not in a later
triage.**

D17 and D20 sat open in `DEFERRED.md` for sessions after their defects were
fixed - anchor-behind had become `info`, `resume()` had been wired and was
running on every task. Both were found by a triage that read the code rather
than the entry.

A pile that carries closed items is a pile people stop reading, which is
exactly the harm DR1's permanent false criticals caused in `alpha verify`: a
real entry gets scrolled past because the last twenty were noise. The record
of a defect and the state of the code are the same fact, and they move
together or the record stops being one.
