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
  | write_file       | yes          | USER          |
  | memory_write     | yes          | USER          |
  | memory_consolidate | yes        | USER          |
  | run_code         | yes          | USER          |
  | append_to_outbox | yes          | USER          |
  | git_push         | yes          | TRUSTED       |
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..kernel.types import Taint

MAX_READ_CHARS = 200_000


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
        libs = _library_manual()
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
    taint = ctx.taint_tracker(task.id).taint_of(text) if task is not None         else Taint.USER
    try:
        out = ctx.memory.write(tier, text, taint)
    except UntrustedMemoryRefused as e:
        return {"ok": False, "error": str(e), "taint": taint.value}
    except (MemoryTooLarge, ValueError) as e:
        return {"ok": False, "error": str(e)}

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
        return {"ok": False,
                "error": (f"artifact {id} belongs to another task attempt and "
                          "is out of scope for this run")}
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
    """Real fetches are opt-in. Everything returned is UNTRUSTED by construction."""
    fetcher = getattr(ctx, "web_fetcher", None)
    if fetcher is None:
        return {
            "ok": False,
            "error": "no web fetcher configured; set ctx.web_fetcher to enable",
        }
    body = fetcher(url)
    return {"ok": True, "url": url, "content": body}


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
