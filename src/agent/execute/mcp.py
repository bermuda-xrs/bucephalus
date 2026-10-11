from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time

from ..kernel.types import Taint
from ..proc import kill_tree
from .hooks import _env
from .tools import ToolSpec

PROTOCOL = "2024-11-05"
MAX_RESULT_CHARS = 20000
_SAFE = re.compile(r"[^A-Za-z0-9_]")


class McpError(RuntimeError):
    pass


class McpServer:
    def __init__(self, name: str, cfg, cwd: str):
        self.name, self.cfg, self.cwd = name, cfg, cwd
        self.proc: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0
        self.stderr_tail = ""

    def start(self) -> None:
        env = {**_env(), **self.cfg.env}
        try:
            self.proc = subprocess.Popen(  # noqa: S603
                self.cfg.argv, cwd=self.cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1)
        except OSError as e:
            raise McpError(f"could not start: {e}") from e
        threading.Thread(target=self._read, daemon=True, name=f"mcp-{self.name}").start()
        threading.Thread(target=self._drain, daemon=True, name=f"mcp-err-{self.name}").start()
        try:
            self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                        "clientInfo": {"name": "bucephalus", "version": "1"}},
                         self.cfg.start_timeout_s)
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except McpError:
            self.close()
            raise

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                self._lines.put(json.loads(line))
            except ValueError:
                continue
        self._lines.put(None)

    def _drain(self) -> None:
        for line in self.proc.stderr:
            self.stderr_tail = (self.stderr_tail + line)[-2000:]

    def _send(self, obj: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(obj) + chr(10))
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise McpError(f"the server is not running: {e}") from e

    def request(self, method: str, params: dict, timeout_s: float):
        if not self.running:
            raise McpError("the server is not running")
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    msg = self._lines.get(timeout=max(0.0, deadline - time.monotonic()))
                except queue.Empty:
                    self.close()
                    raise McpError(f"timed out after {timeout_s}s; the server was stopped") from None
                if msg is None:
                    raise McpError("the server exited" + (f": {self.stderr_tail.strip()[-300:]}" if self.stderr_tail else ""))
                if "method" in msg:
                    self._answer(msg)
                elif msg.get("id") == rid:
                    if "error" in msg:
                        raise McpError(str(msg["error"].get("message", msg["error"]))[:300])
                    return msg.get("result") or {}

    def _answer(self, msg: dict) -> None:
        if "id" not in msg:
            return
        if msg["method"] == "ping":
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        else:
            self._send({"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32601, "message": "this client offers no " + str(msg["method"])}})

    def list_tools(self) -> list[dict]:
        out, cursor = [], None
        while True:
            res = self.request("tools/list", {"cursor": cursor} if cursor else {}, self.cfg.start_timeout_s)
            out += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return out

    def call(self, tool: str, args: dict) -> dict:
        try:
            res = self.request("tools/call", {"name": tool, "arguments": args}, self.cfg.timeout_s)
        except McpError as e:
            return {"ok": False, "error": str(e)}
        parts = [c.get("text", "") if c.get("type") == "text" else f"[{c.get('type', 'content')} omitted]"
                 for c in res.get("content", [])]
        text = chr(10).join(parts)[:MAX_RESULT_CHARS]
        if res.get("isError"):
            return {"ok": False, "error": text or "the tool reported an error"}
        return {"ok": True, "content": text}

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            kill_tree(self.proc.pid)
        if self.proc is not None:
            for f in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                try:
                    f.close()
                except (OSError, ValueError):
                    pass


def _schema(raw: dict) -> dict:
    props = {}
    for k, v in (raw.get("properties") or {}).items():
        spec = {"type": v.get("type", "string")} if isinstance(v, dict) else {"type": "string"}
        if isinstance(v, dict) and isinstance(v.get("enum"), list):
            spec["enum"] = v["enum"]
        props[_SAFE.sub("_", str(k))] = spec
    return {"type": "object", "properties": props, "required": [r for r in raw.get("required", []) if r in props]}


def attach(ctx, task) -> None:
    cfgs = ctx.config.mcp_servers
    if not cfgs or getattr(ctx, "_mcp_attached", False):
        return
    ctx._mcp_attached = True  # noqa: SLF001
    if ctx.tool_profile == "small":
        ctx.log.append(task.id, "mcp.skipped", {"why": "the small tool kit stays small"})
        return
    for name, cfg in cfgs.items():
        server = McpServer(name, cfg, str(ctx.config.root))
        try:
            server.start()
            tools = server.list_tools()
        except McpError as e:
            ctx.log.append(task.id, "mcp.failed", {"server": name, "why": str(e)[:300]})
            continue
        ctx.mcp_servers.append(server)
        added = 0
        for t in tools:
            tool = str(t.get("name", ""))
            full = f"mcp__{_SAFE.sub('_', name)}__{_SAFE.sub('_', tool)}"
            if not tool or ctx.tools.get(full) is not None:
                continue
            writes = tool not in cfg.read_only
            ctx.tools.register(ToolSpec(
                full, _caller(server, tool), _schema(t.get("inputSchema") or {}),
                side_effecting=writes,
                max_arg_taint=Taint.USER if writes else Taint.UNTRUSTED,
                output_taint=Taint.UNTRUSTED,
                description=f"{tool} of the user-configured MCP server {name}"))
            added += 1
        ctx.log.append(task.id, "mcp.started", {"server": name, "tools": added})


def _caller(server: McpServer, tool: str):
    def call(ctx, task, **args) -> dict:
        return server.call(tool, args)

    return call


def close_all(ctx) -> None:
    for s in getattr(ctx, "mcp_servers", []):
        s.close()

