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
