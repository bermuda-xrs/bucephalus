"""M5.1 — code as action, but not always.

Code-as-action shows real gains on compositional multi-tool work (roughly a
fifth higher success, about a third fewer turns) — but a 2026 study found
under 3% difference on ordinary coding-agent tasks. So it is a judgment call
per step, not a blanket default.

Tools are exposed inside the sandbox through a generated preamble defining
call_tool(name, **kwargs), which round-trips to the parent over stdout/stdin
so every tool call stays inside the capability gate. A sandbox that could call
tools directly would be a hole straight through M8.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..kernel.receipts import execute_once, idempotency_key
from ..security.capabilities import gate_tool_call, render_preview
from .sandbox import TOOLCALL_MARKER, run_sandboxed


@dataclass
class StepPlan:
    tool_calls: int = 0
    has_iteration: bool = False
    has_conditional_chain: bool = False


def choose_action_mode(step_plan: StepPlan) -> str:
    if step_plan.tool_calls >= 3:
        return "code"
    if step_plan.has_iteration:
        return "code"
    if step_plan.has_conditional_chain:
        return "code"
    return "direct"  # single atomic action — code adds risk for nothing


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
    """Every call_tool request from the sandbox passes through here.

    The gate runs exactly as it does for a direct action, side effects are
    receipted, and an action that would need a human is REFUSED rather than
    parked: a sandboxed process cannot be suspended mid-run, so the agent is
    told to issue it as a direct action instead.
    """

    def handler(name: str, args: dict) -> dict:
        spec = ctx.tools.get(name)
        if spec is None:
            raise ToolCallDenied(f"no such tool {name!r}")

        decision = gate_tool_call(ctx, task, spec, args, phase=ctx.phase)
        if decision.verdict == "deny":
            raise ToolCallDenied(decision.reason)
        if decision.verdict == "needs_approval":
            raise ToolCallDenied(
                f"{name} needs approval and cannot be approved from inside a "
                "sandboxed program. Issue it as a direct <action> instead."
            )

        if spec.side_effecting:
            key = idempotency_key(task.id, step, name, args)
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
            return result

        return spec.fn(ctx, task, **args)

    return handler


def run_code_action(
    ctx, task, code: str, timeout_s: int = 30, step: int = 0, live_tools: bool = True
) -> dict:
    """Execute a code action with the tool bridge wired to the capability gate."""
    # Pass the relative names too, so `work/E01` appears inside the sandbox as
    # `work/E01` and not as `E01`. The agent is told to use the former.
    scope = [(str(ctx.config.resolve(s)), s.replace("\\", "/").strip("/"))
             for s in task.contract.artifact_scope]
    # Refuse to execute code that is not Python, and say so distinctly.
    #
    # Measured in the pass-1 traces: 10 of 30 tasks emitted a stray closing
    # brace at the END of the `code` field -
    #     print('Answer written to', output_path)}
    # byte-identical across unrelated tasks. Constrained decoding guarantees
    # the JSON ENVELOPE is valid, so that brace is content the model put
    # inside the string; the wrapper does not add it.
    #
    # This is NOT parsed leniently. Stripping the brace would let a malformed
    # code block score like a wrong answer, and that confusion is exactly what
    # produced a 10% pass rate that read as task difficulty. Instead the code
    # is compiled first and a syntax error becomes its own outcome, named as
    # a malformed action rather than buried in a subprocess stderr.
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
    result = run_sandboxed(
        code,
        allowed_paths=scope,
        timeout_s=timeout_s,
        preamble=CALL_TOOL_PREAMBLE,
        tool_handler=handler,
    )
    result["ok"] = result["returncode"] == 0
    return result
