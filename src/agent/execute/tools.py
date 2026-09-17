"""M5.2 / M8.2 — the tool registry and the taint table.

The taint table is the load-bearing part. Fill it in before enabling anything
unattended; a tool whose max_arg_taint is too generous is a hole the model
cannot be prompted out of walking through.

  | tool           | side_effecting | max_arg_taint |
  | read_file      | no             | UNTRUSTED     |
  | list_dir       | no             | UNTRUSTED     |
  | expand_artifact| no             | UNTRUSTED     |
  | search_history | no             | UNTRUSTED     |
  | memory_read    | no             | UNTRUSTED     |
  | web_fetch      | no             | UNTRUSTED     |
  | web_search     | no             | UNTRUSTED     |
  | glob           | no             | UNTRUSTED     |
  | grep           | no             | UNTRUSTED     |
  | parallel       | no             | UNTRUSTED     |
  | write_file       | yes          | USER          |
  | edit_file        | yes          | USER          |
  | memory_write     | yes          | USER          |
  | memory_consolidate | yes        | USER          |
  | run_code         | yes          | USER          |
  | append_to_outbox | yes          | USER          |
  | git_push         | yes          | TRUSTED       |
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..kernel.types import Taint

MAX_READ_CHARS = 200_000
# A search returning fifty matches into context defeats the ledger
# discipline; so does a grep. Bounded per file AND overall, because one
# pathological file should not consume the whole budget.
MAX_GLOB_HITS = 200
MAX_GREP_HITS = 100
MAX_GREP_PER_FILE = 20
# A batch bigger than this puts more into one observation than the ledger
# discipline allows, which is the same reason grep is bounded.
MAX_PARALLEL_CALLS = 12


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., dict]
    schema: dict
    side_effecting: bool
    max_arg_taint: Taint
    output_taint: Taint
    required_capability: str | None = None
    description: str = ""

    def manual_line(self) -> str:
        args = ", ".join(sorted(self.schema.get("properties", {})))
        effect = "writes" if self.side_effecting else "reads"
        return f"  {self.name}({args}) — {self.description} [{effect}]"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.schema,
            }
            for t in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    def manual(self) -> str:
        """Rendered into the cache-stable prefix — must be deterministic."""
        lines = [
            t.manual_line() for t in sorted(self._tools.values(), key=lambda t: t.name)
        ]
        # Conditional on the TOOL, not only on the libraries. This block
        # describes what may be imported INSIDE run_code, so a registry
        # without run_code that still printed it would put an instruction
        # about a missing facility into the prefix - and G1's code-as-action
        # arm would then be measuring a model reaching for something it does
        # not have, which is adjacent to the question rather than it.
        libs = _library_manual() if "run_code" in self._tools else ""
        if libs:
            # Aimed at the measured failure mode. The harness-tax work
            # found a 3.4B answering immediately with no working and
            # getting the arithmetic wrong (FINDINGS 1.6), and Phase 2B
            # found its failures are overwhelmingly unstructured -
            # negative escape velocities, 5e12, 0.0 (FINDINGS 1.13).
            # Offloading exact computation to a symbolic engine targets
            # that directly: sympy does not make arithmetic slips, and
            # it converts units without a remembered factor.
            lines.append("")
            lines.append("Inside run_code you may also import:")
            lines.append(libs)
            lines.append(
                "  Use them for exact arithmetic or unit conversion "
                "rather than computing it in your head.")
        return "\n".join(lines)

    def read_only(self) -> "ToolRegistry":
        r = ToolRegistry()
        for t in self._tools.values():
            if not t.side_effecting:
                r.register(t)
        return r


def _library_manual() -> str:
    """Sandbox libraries, if the registry is importable.

    Tolerant on purpose: a missing registry degrades to "no
    libraries listed" rather than breaking the cache-stable prefix
    that every prompt in the system is built from.
    """
    try:
        from .libraries import REGISTRY
        return REGISTRY.manual()
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------
# builtin tools. fn(ctx, task, **args) -> dict
# --------------------------------------------------------------------------


def _resolve(ctx, path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path(ctx.config.root) / p


def tool_read_file(ctx, task, path: str) -> dict:
    p = _resolve(ctx, path)
    if not p.exists():
        return {"ok": False, "error": f"no such file: {path}"}
    text = p.read_text(encoding="utf-8", errors="replace")[:MAX_READ_CHARS]
    return {"ok": True, "path": str(p), "content": text, "bytes": p.stat().st_size}


def tool_list_dir(ctx, task, path: str = ".") -> dict:
    p = _resolve(ctx, path)
    if not p.is_dir():
        return {"ok": False, "error": f"not a directory: {path}"}
    entries = sorted(
        (f"{e.name}/" if e.is_dir() else e.name) for e in p.iterdir()
    )
    return {"ok": True, "path": str(p), "entries": entries}


def tool_memory_read(ctx, task, tier: str = "memory") -> dict:
    """A1 tier 1. Read what is already in the prefix.

    Redundant on purpose: tier 1 is ALREADY in the prompt, so this exists for
    the case where the model wants to check what it will be writing against
    before it writes. It reads the FROZEN snapshot rather than the files, so
    the answer matches what the model can actually see this turn — reading
    the live file would show a write that is not yet in context and invite
    the model to reason about a prompt it does not have.
    """
    from ..memory.store import MemoryStore

    if tier not in MemoryStore.FILES:
        return {"ok": False, "error": f"unknown memory tier {tier!r}; expected "
                                      f"one of {sorted(MemoryStore.FILES)}"}
    frozen = ctx.freeze_memory(task.id) if task is not None else {}
    return {"ok": True, "tier": tier, "content": frozen.get(tier, ""),
            "note": ("this is the snapshot taken at task start, which is what "
                     "is in your prompt; writes this turn are not in it")}


def tool_memory_write(ctx, task, text: str, tier: str = "memory") -> dict:
    """A1 tier 1, the write path. USER ceiling, like write_file.

    Two walls, and the outer one is the real one:

      * `max_arg_taint=USER` means the capability gate refuses untrusted
        arguments before this function is entered. That is the injection
        defence and it is the same ceiling `write_file` has.
      * `MemoryStore.write` refuses `Taint.UNTRUSTED` again, for a caller
        that reaches the store without the gate.

    THE WRITE DOES NOT AFFECT THIS TURN. It lands on disk immediately and
    becomes visible at the next session or compression boundary, because the
    prefix is built from a snapshot frozen at task start. Saying so in the
    result is not decoration: a model told nothing would reasonably assume
    the fact is now in context and skip repeating it.
    """
    from ..memory.store import MemoryStore, MemoryTooLarge, UntrustedMemoryRefused

    if tier not in MemoryStore.FILES:
        return {"ok": False, "error": f"unknown memory tier {tier!r}"}
    if getattr(ctx.config, "isolate_retrieval", False):
        return {"ok": False,
                "error": ("persistent memory is disabled for measurement "
                          "runs: a fact written by one arm of a paired trial "
                          "would be in the other arm's prompt")}
    taint = (ctx.taint_tracker(task.id).taint_of(text)
             if task is not None else Taint.USER)
    try:
        out = ctx.memory.write(tier, text, taint)
    except UntrustedMemoryRefused as e:
        return {"ok": False, "error": str(e), "taint": taint.value}
    except (MemoryTooLarge, ValueError) as e:
        return {"ok": False, "error": str(e)}

    from ..memory.store import record_fingerprint

    record_fingerprint(ctx.memory)
    if task is not None:
        ctx.log.append(task.id, "memory.written",
                       {"tier": tier, "taint": taint.value,
                        "fingerprint": ctx.memory.fingerprint(),
                        "consolidated": len(out["consolidated"])})
    out["ok"] = True
    out["visible"] = "next session or compression boundary, not this turn"
    return out


def tool_memory_consolidate(ctx, task, tier: str = "memory") -> dict:
    """De-duplicate, then evict oldest-first only if still over the cap.

    Consolidation rather than truncation is the requirement, and the order is
    the whole of it: evicting first would throw away a fact to make room for
    its own duplicate. Every eviction is appended to the chain, so nothing
    leaves tier 1 without a record.
    """
    from ..memory.store import MemoryStore

    if tier not in MemoryStore.FILES:
        return {"ok": False, "error": f"unknown memory tier {tier!r}"}
    if getattr(ctx.config, "isolate_retrieval", False):
        return {"ok": False, "error": "persistent memory is disabled for "
                                      "measurement runs"}
    out = ctx.memory.consolidate(tier)
    from ..memory.store import record_fingerprint

    record_fingerprint(ctx.memory)
    if task is not None:
        ctx.log.append(task.id, "memory.consolidated",
                       {"tier": tier, "dropped": len(out["dropped"]),
                        "fingerprint": ctx.memory.fingerprint()})
    out["ok"] = True
    return out


def tool_search_history(ctx, task, query: str, limit: int = 5,
                        offset: int = 0) -> dict:
    """B4 — search prior sessions.

    OUTPUT IS UNTRUSTED, unconditionally. The index holds `answer` artifacts
    that carry `taint=trusted` in their own table, and propagating that would
    be the hole: an answer is trusted as the output of a verified run, not as
    input to a later one, and its text may quote a page the agent fetched.
    Pulling old content into a fresh context is a read, not a memory.

    Bounded on purpose. `search.MAX_LIMIT` caps what any query can pull back,
    because a tool that can paste fifty matches into the context defeats the
    ledger discipline that makes the rest of this system affordable.
    """
    from ..kernel import search as search_mod
    from ..kernel.scope import visible_tasks

    try:
        hits = search_mod.search(ctx.conn, query, limit=limit, offset=offset,
                                 allowed_tasks=visible_tasks(ctx, task))
    except search_mod.SearchUnavailable as e:
        return {"ok": False, "error": str(e)}
    except ValueError as e:
        # A malformed FTS5 query is a bad tool ARGUMENT, not a crash. Hand it
        # back the way every other bad argument is handled.
        return {"ok": False, "error": str(e)}

    out = []
    for h in hits:
        d = h.as_dict()
        if h.source == "contract":
            d["snippet"] = search_mod.summarise_contract(
                ctx.conn.execute(
                    "SELECT contract_json FROM tasks WHERE id = ?", (h.ref,)
                ).fetchone()[0])
        out.append(d)
    return {
        "ok": True,
        "query": query,
        "results": out,
        "count": len(out),
        "truncated": len(out) == min(int(limit), search_mod.MAX_LIMIT),
        "taint": "untrusted",
        "note": ("results are UNTRUSTED: this is prior content re-entering a "
                 "new context, not a trusted memory"),
    }


def tool_expand_artifact(ctx, task, id: str) -> dict:  # noqa: A002 - model-facing name
    from ..ledger.artifacts import read_artifact

    from ..kernel.scope import visible_tasks

    art = read_artifact(ctx.conn, id)
    if art is None:
        return {"ok": False, "error": f"no such artifact: {id}"}

    # Retrieval isolation. An artifact id is a content hash, so a benchmark
    # arm cannot usually guess one — but `search_history` RETURNS ids in its
    # `ref` field, and an id also survives the workspace reset, which wipes
    # files and not rows. Scoping the search alone would leave the reference
    # path open, so the check goes on the read itself.
    allowed = visible_tasks(ctx, task)
    if allowed is not None and art.get("task_id") not in allowed:
        # The row's task_id is the FIRST writer's, and artifacts are
        # content-addressed, so a second task producing identical bytes does
        # not own the row it created. Authorship comes from the event log.
        from ..kernel.scope import wrote_artifact

        if not wrote_artifact(ctx.conn, allowed, id):
            return {"ok": False,
                    "error": (f"artifact {id} belongs to another task attempt "
                              "and is out of scope for this run")}
    return {
        "ok": True,
        "id": id,
        "content": art["content"],
        "taint": art["taint"].value,
    }


def tool_write_file(ctx, task, path: str, content: str) -> dict:
    p = _resolve(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(p), "bytes": len(content), "overwrote": existed}


def tool_run_code(ctx, task, code: str, timeout_s: int = 30) -> dict:
    from .codeact import run_code_action

    return run_code_action(ctx, task, code, timeout_s=timeout_s)


def tool_web_fetch(ctx, task, url: str) -> dict:
    """Fetch a URL. Everything returned is UNTRUSTED by construction.

    NOT summarised here. Every tool output already goes through
    `condense_observation`, which quarantines when the taint is UNTRUSTED —
    no tools in that call, only a reference crossing back. Wiring into the
    existing path rather than around it is deliberate: a second
    summarisation path would be a second place for the quarantine to be
    forgotten, and the whole M8 defence keys off provenance this path
    establishes.

    `ctx.web_fetcher` stays overridable so tests need no network, but the
    default is now a real fetcher rather than an error. G4 could not ground
    anything while retrieval defaulted to "not configured".
    """
    fetcher = getattr(ctx, "web_fetcher", None)
    if fetcher is not None:
        return {"ok": True, "url": url, "content": fetcher(url)}

    from ..net.fetch import FetchError, fetch_with_provenance

    try:
        got = fetch_with_provenance(url)
    except FetchError as e:
        # A refusal is an observation the agent should see and route around,
        # not an exception that kills the task. robots.txt refusals land here
        # too, and the message says which rule refused.
        return {"ok": False, "url": url, "error": str(e)}
    return {"ok": True, "url": url, "content": got["text"],
            "provenance": got["provenance"]}


def tool_parallel(ctx, task, calls: list | None = None, step: int = 0) -> dict:
    """A5 — run several READ-ONLY tool calls at once.

    Reading five files concurrently is faster regardless of how many model
    instances exist, and that is the whole of the win being claimed. What is
    NOT here is equally deliberate:

    **No model calls, at any pool size.** One Ollama instance on CPU inference:
    concurrent generation contends for the same cores, and wall clock is the
    only cost axis this project has. Parallelising the model would not make
    the system faster, it would make the measurement meaningless.

    **No side-effecting tools.** Idempotency receipts assume ordered
    execution, and two writes racing for the same receipt key is a corrupt
    state rather than a slow one. Refused for the whole batch, not skipped.

    **EVERY call is gated BEFORE ANY call runs.** Concurrency is a new way for
    a check to be skipped for one call while passing for another, so the
    checks are not merely repeated per call — they all happen first, and one
    refusal means nothing executes. A batch that partially applied would be a
    worse outcome than a batch that failed: the model would see some results
    and no clear signal that the rest were refused.
    """
    from concurrent.futures import ThreadPoolExecutor

    from ..security.exposure import UNRESTRICTED, ToolNotExposed
    from ..security.exposure import check as exposure_check

    calls = list(calls or [])
    if not calls:
        return {"ok": False, "error": "parallel needs a non-empty `calls` list"}
    width = max(1, int(getattr(ctx.config.limits, "max_parallel_tools", 4)))
    if len(calls) > MAX_PARALLEL_CALLS:
        return {"ok": False,
                "error": (f"{len(calls)} calls exceeds the cap of "
                          f"{MAX_PARALLEL_CALLS}; a batch that large puts more "
                          "into context than the ledger discipline allows")}

    # ---- phase 1: resolve and gate EVERYTHING. Nothing has run yet.
    from ..security.capabilities import gate_tool_call

    planned = []
    for i, c in enumerate(calls):
        if not isinstance(c, dict) or not c.get("tool"):
            return {"ok": False, "error": f"call {i} has no `tool`"}
        name = c["tool"]
        args = c.get("args") or {}
        if name == "parallel":
            return {"ok": False,
                    "error": "parallel may not nest; one level is the win"}
        spec = ctx.tools.get(name)
        if spec is None:
            return {"ok": False, "error": f"call {i}: no such tool {name!r}"}
        if spec.side_effecting:
            return {"ok": False, "error": (
                f"call {i}: {name} is side-effecting and may not run in a "
                "batch. Idempotency receipts assume ordered execution.")}
        policy = ctx.tool_policy(task.id) if task is not None else UNRESTRICTED
        try:
            exposure_check(policy, name)
        except ToolNotExposed as e:
            if task is not None:
                ctx.log.append(task.id, "tool.not_exposed",
                               {"tool": name, "step": step, "in_batch": True})
            return {"ok": False, "denied": name, "error": str(e),
                    "applied": False}
        decision = gate_tool_call(ctx, task, spec, args, phase=ctx.phase)
        if decision.verdict != "allow":
            if task is not None:
                ctx.log.append(task.id, "human.denied",
                               {"tool": name, "reason": decision.reason,
                                "in_batch": True})
            return {"ok": False, "denied": name, "error": (
                f"DENIED by the capability gate: {decision.reason}. The whole "
                "batch was refused; nothing ran."), "applied": False}
        planned.append((name, args, spec))

    # ---- phase 2: run. Every call already passed every check.
    def _one(item):
        name, args, spec = item
        try:
            return {"tool": name, "args": args, "result": spec.fn(ctx, task, **args)}
        except Exception as e:  # noqa: BLE001 - one bad call must not kill the batch
            return {"tool": name, "args": args,
                    "result": {"ok": False, "error": f"{type(e).__name__}: {e}"}}

    if width == 1 or len(planned) == 1:
        results = [_one(x) for x in planned]
    else:
        with ThreadPoolExecutor(max_workers=min(width, len(planned))) as pool:
            results = list(pool.map(_one, planned))

    return {"ok": True, "n": len(results), "width": width,
            "results": results, "applied": True}


def tool_web_search(ctx, task, query: str, limit: int = 5) -> dict:
    """Search the web. Titles and snippets are attacker-authored text.

    A result list is a web page that happens to be shaped like data, so the
    output taint is UNTRUSTED for the same reason web_fetch's is.
    """
    searcher = getattr(ctx, "web_searcher", None)
    if searcher is not None:
        results = searcher(query, limit)
        return {"ok": True, "query": query, "results": results,
                "provenance": {"query": query, "n_results": len(results),
                               "retrieval": "override", "fetched_at": time.time()}}

    from ..net.search import SearchError, search

    try:
        results = search(query, limit)
    except SearchError as e:
        # THE DISTINCTION, carried into the record rather than left in an
        # exception string. "The page fetched but parsed to nothing" and "the
        # query matched nothing" look identical to a caller and have opposite
        # fixes; a scraper that silently breaks otherwise reads as a topic
        # with no coverage.
        if task is not None:
            ctx.log.append(task.id, "tool.search_failed",
                           {"query": query[:200], "kind": e.kind,
                            "error": str(e)[:400]})
        return {"ok": False, "query": query, "results": [],
                "failure_kind": e.kind, "error": str(e),
                "provenance": {"query": query, "n_results": 0,
                               "retrieval": f"failed:{e.kind}",
                               "fetched_at": time.time()}}
    return {"ok": True, "query": query, "results": results,
            "provenance": {"query": query, "n_results": len(results),
                           # "searched" and "0 results" together is an honest
                           # miss; "failed:parse_failure" is a broken tool.
                           "retrieval": "searched", "fetched_at": time.time()}}


def tool_glob(ctx, task, pattern: str, path: str = ".") -> dict:
    """Filenames matching a glob, inside artifact_scope."""
    root = _resolve(ctx, path)
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {path}"}
    hits = sorted(str(h.relative_to(root)) for h in root.glob(pattern)
                  if h.is_file())
    return {"ok": True, "path": str(root), "pattern": pattern,
            "matches": hits[:MAX_GLOB_HITS],
            "truncated": len(hits) > MAX_GLOB_HITS, "total": len(hits)}


def tool_grep(ctx, task, pattern: str, path: str = ".",
              glob: str = "**/*") -> dict:
    """Lines matching a regex, with file and line number.

    Bounded twice — matches per file and matches overall — because an
    unbounded grep is a way to put a whole repository into the context
    window one line at a time, which is the ledger discipline defeated by
    accident rather than by attack.
    """
    root = _resolve(ctx, path)
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {path}"}
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"bad regex {pattern!r}: {e}"}

    hits: list[dict] = []
    truncated = False
    for f in sorted(root.glob(glob)):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        per_file = 0
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append({"file": str(f.relative_to(root)), "line": n,
                             "text": line.strip()[:300]})
                per_file += 1
                if per_file >= MAX_GREP_PER_FILE or len(hits) >= MAX_GREP_HITS:
                    truncated = True
                    break
        if len(hits) >= MAX_GREP_HITS:
            break
    return {"ok": True, "path": str(root), "pattern": pattern,
            "matches": hits, "truncated": truncated}


def tool_edit_file(ctx, task, path: str, old: str, new: str,
                   count: int = 1) -> dict:
    """Replace an exact string. Refuses if it is absent or ambiguous.

    Exact-match replacement rather than a diff or a line range, and the
    refusals are the feature. An edit that silently matched nothing reports
    success and changes nothing, which the model then builds on; an edit that
    matched three places when one was meant is worse. Both are the same
    failure this project keeps finding elsewhere — an operation that reports
    a result adjacent to what it actually did.
    """
    p = _resolve(ctx, path)
    if not p.exists():
        return {"ok": False, "error": f"no such file: {path}"}
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found == 0:
        return {"ok": False, "error": (
            f"the exact text to replace was not found in {path}. Read the "
            "file and copy the target text verbatim, including indentation.")}
    if found != count:
        return {"ok": False, "error": (
            f"the text to replace appears {found} times in {path} but count="
            f"{count} was given. Include more surrounding context to make it "
            "unique, or pass count={found} if every occurrence should change.")}
    p.write_text(text.replace(old, new, found), encoding="utf-8")
    return {"ok": True, "path": str(p), "replacements": found,
            "bytes": len(text) - len(old) * found + len(new) * found}


def tool_append_to_outbox(ctx, task, to: str, body: str) -> dict:
    """Write a message to a LOCAL outbox file. Nothing leaves this machine.

    Named for what it does. It was called `send_message` and returned
    `delivered: True`, which was a lie the taint tests happened not to care
    about — an honest name costs nothing and stops the next reader assuming
    mail went out.

    It is still side-effecting and still receipted, because its job is to be a
    real target for the capability gate and the idempotency guard. Swap in a
    real transport here and the surrounding machinery does not change.
    """
    outbox = Path(ctx.config.root) / "runs" / "outbox.log"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    with outbox.open("a", encoding="utf-8") as f:
        f.write(f"{task.id}\t{to}\t{body!r}\n")
    return {"ok": True, "to": to, "written_to": str(outbox), "sent": False}


def tool_git_push(ctx, task, remote: str = "origin", branch: str | None = None) -> dict:
    """Push the current branch. TRUSTED tier, so a human already approved it.

    Real, because the gate makes it safe to be real: `required_capability
    ="trusted"` means this parks for a human every time, and model-supplied
    arguments carry USER taint which is above this tool's ceiling — so the
    only way to reach here is with system-authored arguments and an explicit
    human approval.
    """
    root = Path(ctx.config.root)
    if not (root / ".git").exists():
        return {"ok": False, "error": f"{root} is not a git repository"}

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603
            ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=120
        )

    if branch is None:
        head = _git("rev-parse", "--abbrev-ref", "HEAD")
        if head.returncode != 0:
            return {"ok": False, "error": head.stderr.strip()[:400]}
        branch = head.stdout.strip()

    remotes = _git("remote")
    if remote not in remotes.stdout.split():
        return {
            "ok": False,
            "error": f"no remote named {remote!r}; configured: "
                     f"{remotes.stdout.split() or 'none'}",
        }

    proc = _git("push", remote, branch)
    return {
        "ok": proc.returncode == 0,
        "remote": remote,
        "branch": branch,
        "stdout": proc.stdout.strip()[:2000],
        "stderr": proc.stderr.strip()[:2000],
    }


def _s(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props)}


_STR = {"type": "string"}


def default_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(
        ToolSpec(
            "read_file",
            tool_read_file,
            _s(path=_STR),
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.UNTRUSTED,
            description="read a UTF-8 file from disk",
        )
    )
    r.register(
        ToolSpec(
            "list_dir",
            tool_list_dir,
            {"type": "object", "properties": {"path": _STR}, "required": []},
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.UNTRUSTED,
            description="list the entries of a directory",
        )
    )
    r.register(
        ToolSpec(
            "expand_artifact",
            tool_expand_artifact,
            _s(id=_STR),
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.UNTRUSTED,
            description="pull back the full text of a compacted artifact by id",
        )
    )
    r.register(
        ToolSpec(
            "memory_read",
            tool_memory_read,
            {"type": "object", "properties": {"tier": _STR}, "required": []},
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.TRUSTED,
            description="read persistent memory (already in your prompt)",
        )
    )
    r.register(
        ToolSpec(
            "memory_write",
            tool_memory_write,
            {"type": "object", "properties": {"text": _STR, "tier": _STR},
             "required": ["text"]},
            side_effecting=True,
            # SAME CEILING AS write_file. Tier 1 is rendered into the stable
            # prefix of every future session, so untrusted-derived content is
            # refused rather than queued - see SPEC-DELTA C32.
            max_arg_taint=Taint.USER,
            output_taint=Taint.TRUSTED,
            description="remember a fact for future sessions",
        )
    )
    r.register(
        ToolSpec(
            "memory_consolidate",
            tool_memory_consolidate,
            {"type": "object", "properties": {"tier": _STR}, "required": []},
            side_effecting=True,
            max_arg_taint=Taint.USER,
            output_taint=Taint.TRUSTED,
            description="de-duplicate memory and evict the oldest if over cap",
        )
    )
    r.register(
        ToolSpec(
            "search_history",
            tool_search_history,
            {"type": "object",
             "properties": {"query": _STR,
                            "limit": {"type": "integer"},
                            "offset": {"type": "integer"}},
             "required": ["query"]},
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.UNTRUSTED,
            description="search goals and answers from earlier sessions",
        )
    )
    r.register(
        ToolSpec(
            "web_fetch",
            tool_web_fetch,
            _s(url=_STR),
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.UNTRUSTED,
            description="fetch a URL; the result is always untrusted data",
        )
    )
    r.register(
        ToolSpec(
            "parallel",
            tool_parallel,
            {"type": "object",
             "properties": {"calls": {"type": "array", "items": {
                 "type": "object",
                 "properties": {"tool": _STR, "args": {"type": "object"}},
                 "required": ["tool"]}}},
             "required": ["calls"]},
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            # UNTRUSTED because the batch carries whatever its members return,
            # and the most tainted member decides. Stamping anything lower
            # would launder a web_fetch through a batch.
            output_taint=Taint.UNTRUSTED,
            description="run several read-only tools at once",
        )
    )
    r.register(
        ToolSpec(
            "web_search",
            tool_web_search,
            {"type": "object",
             "properties": {"query": _STR, "limit": {"type": "integer"}},
             "required": ["query"]},
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.UNTRUSTED,
            description="search the web; titles and snippets are untrusted",
        )
    )
    r.register(
        ToolSpec(
            "glob",
            tool_glob,
            {"type": "object",
             "properties": {"pattern": _STR, "path": _STR},
             "required": ["pattern"]},
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.UNTRUSTED,
            description="list files matching a glob pattern",
        )
    )
    r.register(
        ToolSpec(
            "grep",
            tool_grep,
            {"type": "object",
             "properties": {"pattern": _STR, "path": _STR, "glob": _STR},
             "required": ["pattern"]},
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.UNTRUSTED,
            description="search file contents for a regex",
        )
    )
    r.register(
        ToolSpec(
            "edit_file",
            tool_edit_file,
            {"type": "object",
             "properties": {"path": _STR, "old": _STR, "new": _STR,
                            "count": {"type": "integer"}},
             "required": ["path", "old", "new"]},
            side_effecting=True,
            max_arg_taint=Taint.USER,
            output_taint=Taint.TRUSTED,
            description="replace an exact string in a file inside artifact_scope",
        )
    )
    r.register(
        ToolSpec(
            "write_file",
            tool_write_file,
            _s(path=_STR, content=_STR),
            side_effecting=True,
            max_arg_taint=Taint.USER,
            output_taint=Taint.TRUSTED,
            description="write a UTF-8 file inside artifact_scope",
        )
    )
    r.register(
        ToolSpec(
            "run_code",
            tool_run_code,
            _s(code=_STR),
            side_effecting=True,
            max_arg_taint=Taint.USER,
            output_taint=Taint.UNTRUSTED,
            description="run a short python program in the sandbox",
        )
    )
    r.register(
        ToolSpec(
            "append_to_outbox",
            tool_append_to_outbox,
            _s(to=_STR, body=_STR),
            side_effecting=True,
            max_arg_taint=Taint.USER,
            output_taint=Taint.TRUSTED,
            description="write a message to the local outbox file (nothing is sent)",
        )
    )
    r.register(
        ToolSpec(
            "git_push",
            tool_git_push,
            {"type": "object", "properties": {"remote": _STR, "branch": _STR},
             "required": []},
            side_effecting=True,
            max_arg_taint=Taint.TRUSTED,
            output_taint=Taint.TRUSTED,
            required_capability="trusted",
            description="push commits to a remote (always parks for a human)",
        )
    )
    # `install_skill` is deliberately absent. Skills enter through
    # `alpha skills --add` and leave candidacy only through the M9 gate with a
    # human approving; a tool that always returned an error was a decoy.
    return r
