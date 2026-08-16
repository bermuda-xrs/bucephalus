"""M5.2 / M8.2 — the tool registry and the taint table.

The taint table is the load-bearing part. Fill it in before enabling anything
unattended; a tool whose max_arg_taint is too generous is a hole the model
cannot be prompted out of walking through.

  | tool           | side_effecting | max_arg_taint |
  | read_file      | no             | UNTRUSTED     |
  | list_dir       | no             | UNTRUSTED     |
  | expand_artifact| no             | UNTRUSTED     |
  | web_fetch      | no             | UNTRUSTED     |
  | write_file     | yes            | USER          |
  | run_code       | yes            | USER          |
  | send_message   | yes            | USER          |
  | git_push       | yes            | TRUSTED       |
  | install_skill  | yes            | TRUSTED       |
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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
        return "\n".join(
            t.manual_line() for t in sorted(self._tools.values(), key=lambda t: t.name)
        )

    def read_only(self) -> "ToolRegistry":
        r = ToolRegistry()
        for t in self._tools.values():
            if not t.side_effecting:
                r.register(t)
        return r


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


def tool_expand_artifact(ctx, task, id: str) -> dict:  # noqa: A002 - model-facing name
    from ..ledger.artifacts import read_artifact

    art = read_artifact(ctx.conn, id)
    if art is None:
        return {"ok": False, "error": f"no such artifact: {id}"}
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


def tool_send_message(ctx, task, to: str, body: str) -> dict:
    """Stands in for any external send. Side-effecting, so it is receipted."""
    outbox = Path(ctx.config.root) / "runs" / "outbox.log"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    with outbox.open("a", encoding="utf-8") as f:
        f.write(f"{task.id}\t{to}\t{body!r}\n")
    return {"ok": True, "to": to, "delivered": True}


def tool_git_push(ctx, task, remote: str = "origin", branch: str = "main") -> dict:
    return {
        "ok": False,
        "error": "git_push is a trusted-tier action and is not wired up in the alpha",
        "remote": remote,
        "branch": branch,
    }


def tool_install_skill(ctx, task, skill_id: str) -> dict:
    return {
        "ok": False,
        "error": "installing a skill requires human approval via the skill gate (M9)",
        "skill_id": skill_id,
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
            "send_message",
            tool_send_message,
            _s(to=_STR, body=_STR),
            side_effecting=True,
            max_arg_taint=Taint.USER,
            output_taint=Taint.TRUSTED,
            description="send a message to an external recipient",
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
            description="push commits to a remote",
        )
    )
    r.register(
        ToolSpec(
            "install_skill",
            tool_install_skill,
            _s(skill_id=_STR),
            side_effecting=True,
            max_arg_taint=Taint.TRUSTED,
            output_taint=Taint.TRUSTED,
            required_capability="trusted",
            description="install a skill into the store",
        )
    )
    return r
