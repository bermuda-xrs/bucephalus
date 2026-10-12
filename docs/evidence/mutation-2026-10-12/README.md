# Mutation testing of the code added since harness 8 - 2026-10-12

`scripts/mutate.py`, each module against the tests named in `run.sh`. `before/` is the first run (the code as it stood
before any mutant-killing test); `after/` is the last run of each module. Module sizes grew in between (the audit fixes),
so the mutant counts differ.

| module | before | after |
|---|---|---|
| `execute/tools.py` (profiles, apply_patch) | 51/69, 73.9% | 66/69, 95.7% |
| `config.py` | 8/8 | 8/8 |
| `execute/loop.py` (stuck, read_first, caps, window) | 21/24, 87.5% | 20/21 + 3 timeouts, 95.2% |
| `execute/rewind.py` | 28/34, 82.4% | 34/34 |
| `execute/shadow.py` | 24/41, 58.5% | 44/60, 73.3% |
| `execute/hooks.py` | 19/27, 70.4% | 21/28, 75.0% |
| `execute/mcp.py` | 50/86 + 7 timeouts, 58.1% | 104/130 + 1 timeout, 80.0% |
| `security/capabilities.py` (path gate) | 53/88, 60.2% | 106/117, 90.6% |
| `context.py` (AGENTS.md) | 18/21 | 19/21 |
| `execute/diffview.py` | 14/18 | 17/18 |
| `execute/say.py` | 14/17 | 15/17 |
| `channel/telegram.py` (queue, callbacks) | 42/54 | 49/54 |
| `execute/schema.py` | 9/11 | 11/11 |

Found by the new tests and fixed: an MCP schema `type` given as a list failed the whole server; `McpServer.close()`
never reaped the process; `_looks_like_path` was dead code. The Telegram cross-chat approval refusal had no test.

The caps "after" run uses the whole gate suite (the "before" run used four files), so part of that gain is the wider
test set.

Suite wall time on this laptop, `python -m pytest tests -q`: 2:59 in the previous session's note (conditions not
recorded); 90 s at the start of this session (1719 passed); 91 s after the new tests (1808 passed), nothing else running.

## The survivors, and why they stay

- **Limit constants** (`MAX_RESULT_CHARS`, `MAX_PAGES`, `MAX_TOOLS`, `MAX_LINE`, `MAX_HOOK_TEXT`, `MAX_LEN`,
  `MAX_DIFF_CHARS`, `BIG_FILE_BYTES`, `GIT_TIMEOUT_S`): the tests read the constant, so moving it moves the
  expectation. A test pinning the literal would only restate it.
- **Log fields and message cuts** (`[:300]`, `[:80]`, `ms` arithmetic, `count`, the 2000-character stderr tail): not
  behaviour.
- **Timing** (`p.wait(5)`, `proc.wait(5)`, `w.join(60)`): a different bound changes no outcome a test can see.
- **Equivalent**: `return ""` vs `None`, `return False` vs `None` where the caller tests truthiness; `_next_id`'s start
  and step (ids stay unique); daemon flags on reader threads; `text=True` with an explicit encoding; `mkdir(parents=True)`
  where the parent exists; `0x400` vs `0x401` for the reparse bit; `range(33)` vs `range(34)` in the URI strip; the
  4096-character and NUL guards in `_names_something` (the path join refuses the same inputs a step later); the
  backslash in `_pathish_value` (`isabs` catches it on Windows); defaults of `by_value` (every caller passes it).
- **Masked pairs**: the two "server is not running" raises in `McpServer._send`/`request` cover each other.
- **Environment probe**: `git_available()` (the tests skip without git).
- `_links` descending into directories with `follow_symlinks` flipped (shadow 97): a junction is found either way on
  Windows; it differs only for a symlinked directory, which these tests do not create without admin rights.
