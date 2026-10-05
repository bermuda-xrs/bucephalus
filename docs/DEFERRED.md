# Deferred: open issues only

An entry is removed in the change that closes it; its history is in git and CHANGELOG.

| entry | state |
|---|---|
| D2 | open: diagnosis, no measurement at risk |
| D16 | open: needs a live model per backend |
| D26 | open: needs a bot token and an API key (the user's); D25 and D31 close with it |
| D27 | open: the bot's queue, with D26 |
| D32 | known and accepted |
| D33 | open: a HARNESS_VERSION change; its criterion is measured at zero |

---

## D2 — The cause of the pathological model call is unknown

**Severity:** blocking for the cost axis, mitigated but not diagnosed.

Three occurrences: 2465 s (v3 ladder probe), 2539 s for 512 tokens in L6
(~0.2 tok/s against a normal 2–3), and 1499.8 s historically. That is 29×,
17× and 18× their run medians.

The freeze exception added **detection and recovery** — abort below 25% of the
running median, re-run once, exclude on a second stall — so it can no longer
be scored as a task failure. **It does not explain the stall.** Candidates not
yet separated: llama.cpp KV-cache behaviour, OS paging of worker pages,
thermal throttling, OneDrive I/O contention.

**Why deferred:** the measurement is protected. The diagnosis is a research
task in its own right.

---

## D16 — Per-backend context windows are unreachable over the OpenAI protocol

**Severity:** would silently corrupt a measurement if the server is started
without `OLLAMA_CONTEXT_LENGTH`. Mitigated by both launchers setting it.

The OpenAI chat-completions API has no `num_ctx`, so every `openai`-kind
backend gets whatever window the server allocates. One server-wide value
cannot satisfy two backends declaring different `usable_context_window`s —
`config.openai.yaml` has a 3B asking 16384 and an 8B asking 8192 against the
same URL.

Budgeting BELOW the server's window merely wastes it. Budgeting ABOVE it is
the corrupting direction: M3.4's ceiling is computed from a number the server
will not honour, and Ollama truncates without an error.

**Why not detected in code:** the only portable signal is the returned
`prompt_tokens`, and flagging a mismatch means comparing it against this
project's own token count — which is an uncalibrated estimator (D6). A check
whose false-alarm rate is unknown is worse than a documented constraint, so
this is documented.

**Correct version:** either query the server's window through whatever
non-portable route it offers and assert against the config at startup, or
measure the estimator against the server's own counts first (D6) and then
compare `prompt_tokens` to a count that can be trusted. The second unblocks several other things and is the better
order.

SPEC-DELTA C29.

---

## D25 — the Anthropic adapter accepts `on_chunk` and ignores it (UNDER D26: one decision unblocks both)

**Found:** 2026-09-07, while wiring B5 through every adapter.

`AnthropicBackend.complete` takes the `on_chunk` argument so the `Backend`
protocol stays honest, and does nothing with it: that adapter does not stream,
and making it stream means rewriting a request path that has NEVER MADE A LIVE
CALL. Its module docstring already carries the UNVERIFIED banner.

**Consequence:** a `--profile mixed` run shows nothing on any surface until a
turn completes. Local runs — which is every run this project has done — are
unaffected.

**Why deferred:** rewriting an unexercised path to add a feature nobody can
test is how an adapter acquires a second unverified claim. It should be done
in the same session as the first real Anthropic call, so the streaming path
and the request shape are verified together.

**What would make it urgent:** any surface work aimed at a cloud tier, or the
G1 measurement, which needs a paid backend anyway.

**CLOSED 2026-09-13.** The adapter now streams through `messages.stream()`,
forwarding text deltas to `on_chunk`, on ONE path - which also removes a defect
found on the way (C46: the SDK refuses a large non-streaming request outright).
Tested against a fake client only; there is no key. Verification is D26's now.

---

## D26 — the Telegram transport has never made a live call

**Found:** 2026-09-08, by instruction. Phase 8 carries a STOP before a real
bot token.

`HttpTransport` is written and unexercised. Everything above it — the
allowlist, the drop path, task creation, the park, the buttons, the callback,
the re-verification, the finality guard — is driven end to end through
`FakeTransport`, so the LOGIC is tested and the four HTTP calls are not.

**Consequence:** the shape of Telegram's `getUpdates` response, the exact
`inline_keyboard` payload, and the callback_data round trip are asserted
against my reading of the API rather than against the API. Same standing as
the Anthropic adapter (D25), marked the same way in the module docstring.

**Why deferred:** running it needs a real bot token, which is the user's
decision, not mine.

**What closes it:** one session with a token and a private chat. Check first
that `getUpdates` returns `{"ok": true, "result": [...]}` with `update_id`
where this expects it, and that a pressed button delivers `callback_query.data`
unchanged. Those two are the only places a wrong guess is silent rather than
loud.

---

## D27 - one task at a time, and the bot has no queue (the receipt half CLOSED 2026-09-30; the bot's loop stays open)

**Found:** 2026-09-08, building A4.1.

`serve_forever` runs a task inline before polling again, so a second message
arriving during a run waits for the poll rather than being queued, with no
acknowledgement that it is waiting.

**The constraint under it is gone (2026-09-30).** The chain lock was held
across every side effect, so concurrent runs could not exist. `execute_once`
now claims its receipt row before the effect and fills it in after (the
protocol `kernel/db.py` had described as the honest fix): the lock is held for
row writes only; a run that died inside an effect leaves a claim that a replay
reports as `outcome_unknown` rather than repeating (a crash between a send and
its receipt used to send twice); two threads with one key run it once. Tests,
red first: `tests/test_m1_kernel.py` (`test_a_run_that_died_mid_effect_does_not_repeat_it`,
`test_a_slow_effect_does_not_hold_the_chain`, `test_a_failed_effect_can_be_retried`).

**Still open, and why it is not built:** the Telegram loop itself - an
acknowledgement ("queued, one ahead") and a real queue in front of it. It is
the D26 transport, which has never made a live call, so changing it without
one would be untested code on the one path that talks to strangers. It waits
with D26 for a token; the lock no longer stands in its way.

**D26 now holds TWO unexercised live paths, 2026-09-13**, by instruction: the
Telegram transport, and the Anthropic adapter's streaming path (D25, closed as
a gap, open as a claim). They close together whenever a token and a key appear.
The Anthropic half needs one trivial `--profile mixed` task: confirm tokens
reach a surface while the turn is running, that `cache_read_input_tokens` is
non-zero on the second call, and that a refusal is reported rather than
rerouted.

---

## D31 — the Anthropic path has no stall detection (UNDER D26: one decision unblocks both)

**Found:** 2026-09-13, writing D25.

`StallWatch` protects the OpenAI-protocol adapters. The Anthropic adapter never
had it, streaming or not. Wiring it is small, but it would be the second
unverified behaviour added to a path that has made no live call; it belongs in
the same session as D26's verification.

**D30 CLOSED 2026-09-13.** Decided by instruction: 9.2 is withdrawn, not
deferred. The child session is seeded by reference. See SPEC-DELTA C49.

---

## D32 — the sandbox is a door, not a wall (KNOWN AND ACCEPTED 2026-09-16: Phase 6 withdrawn - see FINDINGS 1.29)

**Found:** 2026-09-13, Item 2's sweep (C50).

The sandbox's audit hook now refuses reads, sqlite connections, listings and
process starts against protected state. It lives in the process it polices, so
code written to defeat it - `ctypes`, a native extension - can. It stops a model
that REACHES for a protected file; it does not stop one that is TRYING.

**What closes it:** Phase 6 (A6, a container per run), parked because Docker is
not installed. Until then, isolation holds by construction for every tool route
and by a guard for code execution, and the run record should say so.

**SQLite, 2026-09-14 (C55).** The fence reads a database string as SQLite
reads it and authorizes ATTACH; the authorizer is installed by a wrapped
`sqlite3.connect`. A `Connection` built another way, or
`set_authorizer(None)`, gets past it - the same door.

---

## D33 - tier 1 sits before ~966 tokens of tool schemas on this template

**Found:** 2026-09-13, by 9.6 (SPEC-DELTA C48).

The prefix renders tier 1 inside the system text. The local template appends the
tool schemas after the system text, so any tier-1 change invalidates the whole
schema block: the boundary took 77 s with a memory change against 36 s without.
Anthropic renders tools BEFORE system, where the same change would cost only the
system block, so the layout that is cheap depends on the provider.

**Why not changed:** moving tier 1 out of the system text changes prefix
STRUCTURE, which this project measured moving pass rates (the harness tax). That
is a HARNESS_VERSION change and needs its own STOP.

**Decision, 2026-09-13: not moved.** A memory change becomes visible only at a
session boundary, so the rebuild is paid rarely. Moving tier 1 needs a
`HARNESS_VERSION` bump, and a bump makes every published number incomparable
with every later one. A rare cost does not buy a certain one.

**Revisit when memory-change frequency rises past one threshold: a boundary
rebuild more than once per task on average.** Count `session.opened` rows whose
`memory_sha256` differs from their parent session's, and divide by tasks. The
measurement record predates sessions (283 tasks, 0 `session.opened` rows), so
the rate is unobservable there; the first run that crosses a boundary
produces the first number.

**2026-09-17: the criterion has a number for the first time, and it is ZERO.**
`bench/inventory.py` counts it across every real-model record - the 283-task
`agent.local.db` and the 5-task verification run of 2026-09-16. Sessions have
been OPENED five times and **rotated none**; tier-1 memory has been written
never. So boundary rebuilds per task is 0, against a threshold of 1, and the
decision not to move stands on its own stated terms rather than on nobody
having checked.

**Reconsidered and re-refused on the same day, with a reason the entry did not
have.** The move was proposed as the bump D38 would ride. Two arguments
against, beyond the unmet criterion:
- it trades a RARE cost for a CONSTANT one. Tier 1 leaves the cached system
  block, so it is re-processed on every step, to save ~966 tokens of schema
  re-processing at a boundary that has not yet happened once;
- it changes prefix STRUCTURE, which this project measured moving pass rates
  (the harness tax, FINDINGS 1.6-1.8), and the next thing scheduled to run is
  a G1 measurement. A structural change immediately upstream of a measurement
  is the adjacency class waiting to happen.

D38 took its bump alone instead. **This entry stays OPEN**, and its criterion
is now a thing `bench/inventory.py` re-answers rather than a thing to derive.

---

## D26 addendum, 2026-09-14 - the tests are waiting

Both live paths now have a test that runs the moment the credential exists:
`tests/test_live_credentials.py::test_telegram_transport_live`
(`ALPHA_TELEGRAM_TOKEN`) and `::test_anthropic_streaming_live`
(`ANTHROPIC_API_KEY`, one small paid call per Anthropic backend). Both skip
without it. The same bodies run every suite against local fakes. The STOP is
unchanged: supplying either credential is the user's decision.
