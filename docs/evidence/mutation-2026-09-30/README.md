# Mutation testing of the 2026-09-30 code (iteration 3, continuation)

Same runner and rules as `docs/evidence/mutation-2026-09-28/README.md`
(`scripts/mutate.py`; a survivor is re-run against the whole suite; a timeout
is undecided). `v6/` is the fresh run at e4447b6 - the new code, its own tests
targeted, survivors rechecked. `red-first/` is the new tests
(`tests/test_new_code_mutants.py`) run alone against the same modules.

| module (scope) | v6, before the new tests | after |
|---|---|---|
| `kernel/receipts.py` - claim-then-execute | 16/25 64.0% (+2 timeouts) | 20/24 83.3% (+2 timeouts) |
| `security/capabilities.py` `_protected_elsewhere` and friends | 22/29 75.9% | with the junction tests too: 12 of 15 mutants of `_protected_elsewhere` alone die to the new file; the rest in the notes below |
| `control/panels.py` `integrity_findings` | 12/13 92.3% | 13/13 100% |
| `control/guard.py` | 27/33 81.8% | 30/33 with the new file alone (the other 3 die to `test_m12_serve` and the rest of the suite) |
| `platform/windows.py` `link_names`, `spawn_denied_processes` | 13/18 72.2% | 15/18 83.3% |
| `proc.py` `orphan_pids` | 17/18 94.4% | unchanged |

## What the run found, and fixed

- **The claim sentinel was JSON**, so a tool that returned exactly
  `{"claimed": true}` would be read back as an unknown outcome. It is not JSON
  now: no stored result can equal it. (Test: `a_tool_result_can_never_look_like_a_claim`.)
- **The console's integrity poll would have skipped the memory check**
  if `ctx.memory is not None` were flipped - nothing tested memory through
  the console. It does now.
- The replay's return value, the waiters' return value, the interrupted-effect
  path, an unknown outcome's `ok: False`, the guard's origin check, header and
  query token acceptance, the no-names refusal on POSIX, the invalid-handle
  constant of `link_names` and the frozen count were each unpinned.

## What is left, and why

- **Equivalent:** `_protected_elsewhere`'s `state` vs `real_state` disjunction
  (they differ only through a link, and a link's names come from
  `link_names`); the `lru_cache(maxsize)`; `receipts.has_receipt`'s two
  mutants (dead code, listed in KNOWN_INERT); the two `WinDLL(use_last_error)`
  and buffer-size constants.
- **Accepted:** the receipt-key prefix `key[:12]` in two messages.
- **Undecided, and a kill:** `mine = True -> False` and `if not mine` make the
  first caller wait on its own Event for ever - a hang, which the runner counts
  as undecided; either fails any test that touches `execute_once`.
- **Not measurable here:** `platform/posix.py` (`adopted`, `_stat_fields`) can
  only be mutation-tested on Linux; its tests ran green in WSL2, not mutated.
- **Known bias, as before:** guard mutants that only the subprocess serve test
  catches read as surviving in a targeted run.
