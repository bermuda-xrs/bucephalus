from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..kernel.types import Taint

MAX_READ_CHARS = 200_000
MAX_GLOB_HITS = 200
MAX_GREP_HITS = 100
MAX_GREP_PER_FILE = 20
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
        lines = [
            t.manual_line() for t in sorted(self._tools.values(), key=lambda t: t.name)
        ]
        libs = _library_manual() if "run_code" in self._tools else ""
        if libs:
            lines.append("")
            lines.append("Inside run_code you may also import:")
            lines.append(libs)
            lines.append(
                "  Use them for exact arithmetic or unit conversion "
                "rather than computing it in your head.")
        return "\n".join(lines)

def _library_manual() -> str:
    try:
        from .libraries import REGISTRY
        return REGISTRY.manual()
    except Exception:  # noqa: BLE001
        return ""


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
    from ..memory.store import MemoryStore

    if tier not in MemoryStore.FILES:
        return {"ok": False, "error": f"unknown memory tier {tier!r}; expected "
                                      f"one of {sorted(MemoryStore.FILES)}"}
    frozen = ctx.freeze_memory(task.id) if task is not None else {}
    return {"ok": True, "tier": tier, "content": frozen.get(tier, ""),
            "note": ("this is the snapshot taken at task start, which is what "
                     "is in your prompt; writes this turn are not in it")}


def tool_memory_write(ctx, task, text: str, tier: str = "memory") -> dict:
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
    from ..kernel import search as search_mod
    from ..kernel.scope import visible_tasks

    try:
        hits = search_mod.search(ctx.conn, query, limit=limit, offset=offset,
                                 allowed_tasks=visible_tasks(ctx, task))
    except search_mod.SearchUnavailable as e:
        return {"ok": False, "error": str(e)}
    except ValueError as e:
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


def tool_expand_artifact(ctx, task, id: str) -> dict:  # noqa: A002
    from ..ledger.artifacts import read_artifact

    from ..kernel.scope import visible_tasks

    art = read_artifact(ctx.conn, id)
    if art is None:
        return {"ok": False, "error": f"no such artifact: {id}"}

    allowed = visible_tasks(ctx, task)
    if allowed is not None and art.get("task_id") not in allowed:
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
    fetcher = getattr(ctx, "web_fetcher", None)
    if fetcher is not None:
        return {"ok": True, "url": url, "content": fetcher(url)}

    from ..net.fetch import FetchError, fetch_with_provenance

    try:
        got = fetch_with_provenance(url)
    except FetchError as e:
        return {"ok": False, "url": url, "error": str(e)}
    return {"ok": True, "url": url, "content": got["text"],
            "provenance": got["provenance"]}


def tool_parallel(ctx, task, calls: list | None = None, step: int = 0) -> dict:
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

    def _one(item):
        name, args, spec = item
        try:
            return {"tool": name, "args": args, "result": spec.fn(ctx, task, **args)}
        except Exception as e:  # noqa: BLE001
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
                           "retrieval": "searched", "fetched_at": time.time()}}


def _reachable(ctx, task, f: Path, state) -> bool:
    from ..security.capabilities import is_protected, within_scope

    root = ctx.config.root
    ok, _ = within_scope({"path": str(f)}, task.contract.artifact_scope, root)
    return ok and not is_protected(str(f), root, state)


def _walk_state(ctx, task):
    from ..security.capabilities import task_state_paths

    return task_state_paths(ctx, task)


def tool_glob(ctx, task, pattern: str, path: str = ".") -> dict:
    root = _resolve(ctx, path)
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {path}"}
    state = _walk_state(ctx, task)
    hits = sorted(str(h.relative_to(root)) for h in root.glob(pattern)
                  if h.is_file() and _reachable(ctx, task, h, state))
    return {"ok": True, "path": str(root), "pattern": pattern,
            "matches": hits[:MAX_GLOB_HITS],
            "truncated": len(hits) > MAX_GLOB_HITS, "total": len(hits)}


def tool_grep(ctx, task, pattern: str, path: str = ".",
              glob: str = "**/*") -> dict:
    root = _resolve(ctx, path)
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {path}"}
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"bad regex {pattern!r}: {e}"}

    hits: list[dict] = []
    truncated = False
    state = _walk_state(ctx, task)
    for f in sorted(root.glob(glob)):
        if not f.is_file() or not _reachable(ctx, task, f, state):
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
    outbox = ctx.config.resolve_state("runs") / "outbox.log"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    with outbox.open("a", encoding="utf-8") as f:
        f.write(f"{task.id}\t{to}\t{body!r}\n")
    return {"ok": True, "to": to, "written_to": str(outbox), "sent": False}


def tool_git_push(ctx, task, remote: str = "origin", branch: str | None = None) -> dict:
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


SMALL_TOOLS = ("read_file", "write_file", "search", "run_command", "update_plan")
SMALL_ONLY = ("search", "run_command", "update_plan")


def apply_profile(registry: ToolRegistry, profile: str) -> ToolRegistry:
    keep = SMALL_TOOLS if profile == "small" else tuple(n for n in registry.names() if n not in SMALL_ONLY)
    out = ToolRegistry()
    for name in registry.names():
        if name in keep:
            out.register(registry.get(name))
    return out


def tool_search(ctx, task, pattern: str, path: str = ".", glob: str | None = None,
                names: bool = False) -> dict:
    if names:
        recursive = pattern if ("/" in pattern or "**" in pattern) else f"**/{pattern}"
        return tool_glob(ctx, task, pattern=recursive, path=path)
    return tool_grep(ctx, task, pattern=pattern, path=path, glob=glob or "**/*")


_STATUS_MARK = {"todo": "[ ]", "doing": "[~]", "done": "[x]"}


def render_checklist(items: list) -> str:
    return chr(10).join(f"{_STATUS_MARK[i['status']]} {i['text']}" for i in items)


def tool_update_plan(ctx, task, items: list) -> dict:
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "items must be a non-empty list of {text, status}"}
    clean = []
    for i in items:
        if not isinstance(i, dict) or not isinstance(i.get("text"), str) or i.get("status") not in _STATUS_MARK:
            return {"ok": False, "error": "each item is {\"text\": str, \"status\": todo|doing|done}"}
        clean.append({"text": i["text"][:200], "status": i["status"]})
    return {"ok": True, "checklist": render_checklist(clean[:30])}


def tool_run_command(ctx, task, command: str, timeout_s: int = 60) -> dict:
    from .command import CommandRefused, to_program
    from .codeact import run_code_action

    try:
        code, verification = to_program(command)
    except CommandRefused as e:
        return {"ok": False, "error": str(e)}
    out = run_code_action(ctx, task, code, timeout_s=min(int(timeout_s), 300), live_tools=False)
    out.update(verification=verification)
    return out


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
            "search",
            tool_search,
            {"type": "object",
             "properties": {"pattern": _STR, "path": _STR, "glob": _STR, "names": {"type": "boolean"}},
             "required": ["pattern"]},
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.UNTRUSTED,
            description="search file contents for a regex (or, with names=true, file names for a glob)",
        )
    )
    r.register(
        ToolSpec(
            "update_plan",
            tool_update_plan,
            {"type": "object",
             "properties": {"items": {"type": "array", "items": {
                 "type": "object",
                 "properties": {"text": _STR, "status": {"type": "string", "enum": ["todo", "doing", "done"]}},
                 "required": ["text", "status"]}}},
             "required": ["items"]},
            side_effecting=False,
            max_arg_taint=Taint.UNTRUSTED,
            output_taint=Taint.UNTRUSTED,
            description="keep your own working checklist; the approved plan is not changed",
        )
    )
    r.register(
        ToolSpec(
            "run_command",
            tool_run_command,
            {"type": "object", "properties": {"command": _STR, "timeout_s": {"type": "integer"}},
             "required": ["command"]},
            side_effecting=True,
            max_arg_taint=Taint.USER,
            output_taint=Taint.UNTRUSTED,
            description="run `python FILE`, `python -m MODULE`, `pytest` or `ruff` in the sandbox",
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
    return r
