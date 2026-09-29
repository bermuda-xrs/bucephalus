# Proposals waiting for the user

Changes the unattended run (iteration 3) measured or researched but did not
make: each changes prompt bytes the model sees (a `HARNESS_VERSION` bump), or
changes the user's system, or both. Each says what it costs and how to decide.

## P1 - A kernel-enforced network fence on Windows, without admin: AppContainer

**Today.** The sandbox's network fence is a Python audit hook: `socket.*`
events raise, loopback included (Phase E, E1). It is advisory in the same way
the path fences are (D32): `ctypes` calling `ws2_32` directly, or any native
extension, never raises an audit event. Stated in HANDOVER C4.

**What exists without admin** (researched 2026-09-28, not built):

| mechanism | admin? | non-advisory? | note |
|---|---|---|---|
| Windows Filtering Platform | yes | yes | out |
| Windows Firewall rule per program | yes | yes | out |
| Proxy environment variables | no | **no** | a socket ignores them |
| Job Object | no | - | no network limits exist |
| **AppContainer** | **no** | **yes** | the kernel refuses every socket to a container without network capabilities, loopback included |

**What AppContainer would take.**
1. `CreateAppContainerProfile` once per user: a registry key under HKCU and a
   folder under `%LOCALAPPDATA%\Packages`. Reversible with
   `DeleteAppContainerProfile`. **A persistent change to the user's security
   configuration** - not made unattended.
2. A `CreateProcessW` path with `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`:
   `subprocess.Popen` cannot pass it, so the sandbox needs its own spawn with
   pipes, then the existing suspended -> Job Object -> resume sequence.
3. File access: a container reads almost nothing by default. The interpreter
   (`%APPDATA%\uv\python\...`, **shared with every other uv project**), the
   venv's site-packages (sympy) and each task workspace need an ACL entry for
   the container SID. Either grant it on the shared install - another
   persistent security change - or keep a private copy of the interpreter in
   the data dir (~100 MB) and grant only that.
4. The frozen app's interpreter is its own install directory, which is easier.

**What it buys.** The first non-advisory fence in the sandbox, for network
AND files (a container sees only what it is granted) - much of what D32 says
only a container would give, without Docker. **Estimate: 2-4 days, uncertain**
(the spawn path and the ACL grants are new; every sandbox property test must
then pass through it, frozen too).

**Decide.** Is a registry profile plus ACL grants acceptable on this machine,
and on the shared interpreter or a private copy?

## P2 - What the model is shown every step, and what it costs (3.7)

Measured 2026-09-28 with the in-loop estimator (`agent.tokens.count_tokens`,
uncalibrated against the model's tokenizer - D6; the "~966" in the brief is
a different counter), on the default toolset, a one-line goal and no memory:

| | code_as_action on | off |
|---|---|---|
| tools exposed | 17 | 16 |
| tool schemas sent every step | 1,215 tok | 1,157 tok |
| tool manual in the prefix | 464 tok | 355 tok |
| system contract | 319 tok | 278 tok |
| whole stable prefix | 819 tok | 668 tok |

Per-tool schema cost (on): parallel 108, edit_file 102, search_history 81,
grep 80, append_to_outbox 78, write_file 74, git_push 71, web_search 71,
glob 69, memory_write 69, expand_artifact 61, web_fetch 60,
memory_consolidate 58, read_file 58, run_code 58, memory_read 57, list_dir 54.

**Options, each a HARNESS_VERSION bump (prompt bytes change):**
1. **Expose only the tools a task's profile grants.** A task with no network
   grant is still sent web_fetch and web_search (131 tok); one that never
   pushes is sent git_push (71). Per-profile exposure would cut ~300-450 tok
   per step on a typical local task. Risk: the model cannot ask for a tool it
   cannot see - which is the point of B3 (`tool.not_exposed`), already built.
2. **code_as_action off by default for small local models.** 209 tok per
   step (151 prefix + 58 schema) - and FINDINGS reports that on the 3B,
   offering code-as-action cost capability: with `run_code` removed it took
   the direct action it otherwise skipped (2 tasks, 3 deterministic runs;
   an effect that reproduces, not three independent draws).
3. **Shorter schema descriptions.** Unmeasured gain; the descriptions carry
   the refusal rules the gate enforces, so shortening them is a behaviour
   change to measure, not a free saving.

**Decide.** Which, if any, to run through the G1 harness as a new arm. None
changes a recorded number until then.

## P3 - A conversation's earlier exchanges, given to its next task (4.2, D30)

The console groups tasks into conversations; a follow-up is a new task with
its own plan. D30's option 3 also hands the NEXT task the earlier (goal,
answer) pairs, so "now count them" can mean something. That changes what the
model sees for every follow-up - a prompt change, so not made unattended.
Built, it is: the prior exchanges as a bounded, UNTRUSTED-tainted block in the
tail (they contain model output), never in the stable prefix, compacted with
the rest of the tail. **Decide:** whether follow-ups should see history at all,
and at what bound.

## P4 - Minimize to the system tray (4.6)

Would take: `pystray` and Pillow (two new dependencies - pystray draws its
icon through Pillow), a tray thread beside
pywebview's loop, and a changed rule: "closing the window stops everything"
becomes "Quit from the tray stops everything". The no-orphans guarantee then
rests on the tray's Quit and on the OS logoff path, both of which need their
own tests (a logoff kill is not a window close). **Decide:** whether a
long-running agent should keep running with no window, which is what a tray
icon is for.

## P5 - Grade a program from outside the process it runs in (D79)

`verify_program` runs the grader's `check()` and the graded code in one
interpreter and reads the first verdict marker on stdout, so the graded code
can print a verdict, patch the serializer, or return an always-equal object.
Built, it is: the harness writes the verdict to a file descriptor the grader
opens before importing anything and the graded code is not told about, read
LAST not first; graders compare `type(got) is type(want) and got == want`;
and, for a real boundary, the answer runs in a child process whose results
the grader compares. The first two are small; the third changes every
grader. Each changes what counts as a pass, so it is a HARNESS_VERSION bump
and a re-baseline. **Decide:** whether the held-out measurement must hold
against a graded program that attacks its grader, and if so which of the
three.

