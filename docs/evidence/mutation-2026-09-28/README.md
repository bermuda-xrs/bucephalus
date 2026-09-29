# Mutation testing of the security core - 2026-09-28/29 (iteration 3, 2.3)

**Runner.** `scripts/mutate.py` (DECISIONS U5): one AST change per mutant
(comparison flipped, `and`/`or` swapped, `not` dropped, condition negated, a
boolean or integer constant changed, `+`/`-` swapped, a return made `None`, a
`raise` made `pass`, one member dropped from a literal collection), each run
in a private copy of the tree against the module's own tests. A survivor is
then re-run against the WHOLE suite, which must first pass unmutated; a
timeout counts as neither a kill nor a survivor, and every whole-suite kill
records the test that made it (D78). Score = kills / decided mutants.

**Runs.** `v2/` - targeted tests only, at 5d4b0b9. `v4/` - v2's survivors
re-run against the whole suite, same code. `v5/` - a fresh run at f67897f with
this run's new tests targeted, survivors re-run against the whole suite.
`red-first/` - each new test file alone against the mutants it was written
for, which is how each was shown red.

**Four tests are timing-sensitive under load** and appear as the only
"killer" of some whole-suite kills: `test_a_slow_batch_is_faster_in_parallel_than_in_sequence`,
`test_a_longer_crawl_delay_wins_but_a_shorter_one_does_not`, and the two
reaper tests fixed on 2026-09-29. A kill made only by these is counted as a
SURVIVOR below (the raw files keep the runner's own status).

| module (scope) | v2 targeted | v4 whole suite | v5 final |
|---|---|---|---|
| `security/capabilities.py` - the gate | 106/127 83.5% | 107/127 84.3% | 133/152 87.5%; **138/152 90.8%** with the junction tests (below) |
| `security/taint.py` | 16/21 76.2% | 16/21 76.2% | 16/21 76.2% |
| `kernel/scope.py` - retrieval isolation | 11/15 73.3% | 11/15 73.3% | 13/15 86.7% |
| `kernel/events.py` - the hash chain | 54/90 60.0% | 75/90 83.3% | 81/91 89.0% |
| `kernel/integrity.py` - anchor, sweep | 35/44 79.5% | 35/44 79.5% | 44/47 93.6% |
| `ledger/prefix.py` `prompt_contract` - the allowlist | 12/13 92.3% | 12/13 92.3% | 13/13 100% |
| `assure/mesh.py` `VerificationMesh` - hold-out | 9/16 56.2% | 11/16 68.8% | 13/16 81.2% |
| `context.py` `compact_transcript` - hold-out | 1/6 16.7% | 2/6 33.3% | 3/6 50.0% |
| `assure/verifiers.py` | 111/190 58.4% | 112/190 58.9% | 173/190 91.1% |

v5 has more capabilities mutants than v2 because this run added code there
(D61's real-path checks, S1's stream check, I2's cache). After v5, three
junction tests (`tests/test_junction_escape.py`) killed five of its survivors
- the real-path half of the scope check, the real-path half of the protected
check, and the 2 s cache bucket - each of which the existing junction tests
left alive because they pointed only at the graders, which both checks refuse
(`red-first/junction.json`).

## Every survivor left, and why

**Equivalent - cannot change behaviour:**
- capabilities `PROJECT_STATE` drops 0, 1, 7, 8: names also in
  `PROTECTED_DIRS` / `PROTECTED_FILES`, so dropping one still protects it.
- capabilities three `lru_cache(maxsize=...)` sizes: performance only.
- capabilities `_looks_like_path` `return False` -> `None`: read only as a
  truth value.
- capabilities `_effective_args` `fn is None` -> `return None`: every
  registered `ToolSpec` has a function.
- capabilities `_is_within`'s `return p == root`: unreachable as True -
  `relative_to` succeeds for equal paths.
- capabilities `_escapes`' `startswith(("/", "\\"))` members: on Python 3.11
  `os.path.isabs` already covers both on Windows, and `\` is not a separator
  on POSIX. (Python 3.13 changes `isabs("/x")` on Windows; this becomes
  killable there.)
- `getattr(config, name, False)` defaults where the field always exists:
  capabilities `approve_scoped_writes`, scope `isolate_retrieval` (x2), mesh
  and context `grader_held_out` (x3).
- events `EVENT_TYPES` drops of `artifact.tombstoned`, `skill.promoted`,
  `skill.demoted`, `step.started`, `step.completed`: registered, and no code
  records them (D62 found the same from the inventory side).
- events `events(since_seq=-1)` -> `-2`: sequence numbers start at 0.
- events `last_of_type` (4 mutants) and taint `quarantined_summarize` (5):
  dead code, listed in `tests/test_dead_code_audit.py` KNOWN_INERT.
- verifiers `out.split(marker, 1)` maxsplit: one verdict line either way.

**Accepted - a real change with no property worth a test:**
- output truncations and message slices (verifiers x6, integrity x2, context
  `[:300]`), `observation=out or "..."` (x2), which of two messages the
  memory finding carries (integrity 205), `round(score, 4)` on the record.
- limits and sampling constants: sandbox timeouts (x4), `k=3`,
  `max_tokens=512`, `compact_transcript(limit=40)`.
- capabilities `_names_a_stream`'s `parts[1:]` -> `[2:]`: differs only for a
  stream directly under a drive root, which is only in scope if the scope IS
  a drive root.

**Unverified - needs a tool that is not installed:** verifiers ruff's
`capture_output=True` / `text=True` (x2): ruff is faked in the tests; with a
real ruff either change breaks the output handling.

## Found by killing them

- The hold-out: a missing or raising verifier could pass; the reviewer
  transcript's held-out branch was unpinned; retrieval with no task ids could
  read everything. The verifiers' list, boolean, schema, citation, pytest,
  ruff and execution checks each had a flipped comparison the suite missed.
- **D79**: a program graded by `verify_program` can print its own verdict
  (strict xfail; PROPOSALS P5).
- My own runner, twice: timeouts counted as kills, then (audit B1/B2) the
  recheck's timeouts too and kills with no named killer (D78). The killer
  names are how the timing-test false kills above were seen at all.
