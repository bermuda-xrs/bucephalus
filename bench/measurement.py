from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.context import AgentContext  # noqa: E402


class IsolationNotDeclared(RuntimeError):
    ...


def open_measurement(config, *, profile: str | None, isolate: bool,
                     why: str = "") -> AgentContext:
    ctx = AgentContext.open(config, profile=profile)
    ctx.config.isolate_retrieval = bool(isolate)
    ctx.config.limits.max_parallel_tools = 1
    ctx.config.channels.telegram.enabled = False
    ctx.config.grader_held_out = True
    ctx.config.profile_provenance["grader"] = (
        "held out: the grading verifier's result reached the model as "
        "pass/fail only; its detail went to verify.* events for the operator")
    ctx.measurement_isolation_reason = why or (
        "isolated: a paired trial requires arms to be independent"
        if isolate else "unisolated: declared, and NOT valid for a paired trial")
    return ctx


def assert_isolation_declared(ctx) -> None:
    if getattr(ctx.config, "isolate_retrieval", None) is None:
        raise IsolationNotDeclared(
            "this measurement has not declared an isolation state. Use "
            "bench.measurement.open_measurement(..., isolate=True/False). An "
            "undeclared run cannot be read as a paired trial, because nothing "
            "in its record says whether one arm could see the other's answer."
        )
