"""D18 — the one door a measurement comes through.

Every measurement path used to call `AgentContext.open()` for itself, and
only `noise.py` set an isolation state. `ladder.py` and the calibration path
in `run.py` declared none, so they ran unrestricted. Nothing leaked, because
neither retrieves — but "nothing leaks today" is a fact about the current
tool set, not a property of the system, and Phase 4 has just added five tools
including two that retrieve.

The fix is not three call sites remembering a flag. It is that **you cannot
obtain a context for a measurement without saying what its isolation is**:
`isolate` is keyword-only with no default, so forgetting it is a TypeError at
the call, not a silently unrestricted run three hours later.

`tests/test_tool_exposure.py` holds the other half — no measurement module
may call `AgentContext.open` directly. Together those make it structurally
impossible rather than currently-true, which is what D18 asked for.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.context import AgentContext  # noqa: E402


class IsolationNotDeclared(RuntimeError):
    """A measurement tried to start without stating its isolation."""


def open_measurement(config, *, profile: str | None, isolate: bool,
                     why: str = "") -> AgentContext:
    """Open a context for a MEASUREMENT. `isolate` is not optional.

    isolate=True   a benchmark attempt reads only what it produced itself:
                   no other arm's answer, no earlier run of the same task, no
                   persistent memory. This is what a paired trial requires.

    isolate=False  unrestricted, and legitimate for a path that measures
                   something other than task outcomes — but it must be
                   DECLARED, because a reader of the run record has to be able
                   to tell "isolation was considered and not needed" from
                   "nobody thought about it".
    """
    ctx = AgentContext.open(config, profile=profile)
    ctx.config.isolate_retrieval = bool(isolate)
    ctx.measurement_isolation_reason = why or (
        "isolated: a paired trial requires arms to be independent"
        if isolate else "unisolated: declared, and NOT valid for a paired trial")
    return ctx


def assert_isolation_declared(ctx) -> None:
    """For a path that builds its own context and cannot use the helper."""
    if getattr(ctx.config, "isolate_retrieval", None) is None:
        raise IsolationNotDeclared(
            "this measurement has not declared an isolation state. Use "
            "bench.measurement.open_measurement(..., isolate=True/False). An "
            "undeclared run cannot be read as a paired trial, because nothing "
            "in its record says whether one arm could see the other's answer."
        )
