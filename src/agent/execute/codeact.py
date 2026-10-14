from __future__ import annotations

import json
import os

from ..kernel.receipts import execute_once, idempotency_key, rewind_epoch
from ..security.capabilities import gate_tool_call, render_preview
from ..security.exposure import ToolNotExposed
from ..security.exposure import check as exposure_check
from .sandbox import TOOLCALL_MARKER, run_sandboxed


CALL_TOOL_PREAMBLE = f'''\
import json as _json, sys as _sys

def call_tool(name, **kwargs):
    """Round-trip a tool call to the parent process, which gates it."""
    _sys.stdout.write({TOOLCALL_MARKER!r} + _json.dumps({{"tool": name, "args": kwargs}}) + "\\n")
    _sys.stdout.flush()
    line = _sys.stdin.readline()
    if not line:
        raise RuntimeError("parent closed the tool channel")
    reply = _json.loads(line)
    if not reply.get("ok", False):
        raise RuntimeError("tool call denied: " + str(reply.get("error", "")))
    return reply.get("result")
'''


class ToolCallDenied(RuntimeError):
    pass


def make_tool_handler(ctx, task, step: int = 0):
    def handler(name: str, args: dict) -> dict:
        spec = ctx.tools.get(name)
        if spec is None:
            raise ToolCallDenied(f"no such tool {name!r}")
        try:
            exposure_check(ctx.tool_policy(task.id), name)
        except ToolNotExposed as e:
            raise ToolCallDenied(str(e)) from None

        decision = gate_tool_call(ctx, task, spec, args, phase=ctx.phase)
        if decision.verdict == "deny":
            raise ToolCallDenied(decision.reason)
        if decision.verdict == "needs_approval":
            raise ToolCallDenied(
                f"{name} needs approval and cannot be approved from inside a "
                "sandboxed program. Issue it as a direct <action> instead."
            )

        if ctx.config.hooks.pre_tool:
            from .hooks import run_hooks

            allowed, why = run_hooks(ctx, task, "pre_tool", name, args=args)
            if not allowed:
                ctx.log.append(task.id, "hook.denied", {"tool": name, "step": step, "why": why[:300]})
                raise ToolCallDenied(f"a hook configured by the user refused {name}: {why}")

        if spec.side_effecting:
            key = idempotency_key(task.id, step + rewind_epoch(ctx.conn, task.id), name, args)
            ctx.log.append(
                task.id,
                "tool.intent",
                {"tool": name, "step": step, "key": key, "via": "code",
                 "preview": render_preview(spec, args)},
            )
            result = execute_once(
                ctx.conn, key, name, args, lambda: spec.fn(ctx, task, **args)
            )
            ctx.log.append(
                task.id, "tool.receipt",
                {"tool": name, "key": key, "ok": bool(result.get("ok", True)),
                 "via": "code"},
            )
        else:
            result = spec.fn(ctx, task, **args)

        if ctx.config.hooks.post_tool and isinstance(result, dict):
            from .hooks import run_hooks

            _, text = run_hooks(ctx, task, "post_tool", name, args=args, result=json.dumps(result, default=str)[:2000])
            if text:
                result = {**result, "hook_output_untrusted": text}
        return result

    return handler


def run_code_action(
    ctx, task, code: str, timeout_s: int = 30, step: int = 0, live_tools: bool = True, on_output=None
) -> dict:
    scope = [(str(ctx.config.resolve(s)), s.replace("\\", "/").strip("/"))
             for s in task.contract.artifact_scope]
    try:
        compile(code, "<action>", "exec")
    except SyntaxError as e:
        return {
            "ok": False,
            "malformed_action": True,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "error": (
                f"Your `code` field is not valid Python: {e.msg} at line "
                f"{e.lineno}. Nothing was executed.\n"
                f"  offending line: {(e.text or '').strip()!r}\n"
                "The `code` field must contain ONLY the Python program. Do "
                "not put a closing brace after the last statement - the JSON "
                "object's own brace goes outside the string, and the decoder "
                "adds it for you."
            ),
        }

    handler = make_tool_handler(ctx, task, step) if live_tools else None
    from ..security.capabilities import task_state_paths

    result = run_sandboxed(
        code,
        allowed_paths=scope,
        timeout_s=timeout_s,
        preamble=CALL_TOOL_PREAMBLE,
        tool_handler=handler,
        protected_paths=[str(p) for p in task_state_paths(ctx, task)],
        on_output=on_output,
    )
    result["ok"] = result["returncode"] == 0
    touched = result.get("touched")
    if touched:
        root = os.path.realpath(str(ctx.config.root))
        for e in touched["entries"]:
            p = e["path"]
            if p == root or p.startswith(root + os.sep):
                e["path"] = os.path.relpath(p, root).replace(os.sep, "/")
    return result
