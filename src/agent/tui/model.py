"""What the TUI knows, with no Textual in sight.

Separated so the five screens' CONTENT is testable without driving a terminal.
Every rule that matters here — which colour an alert gets, what a tool trail
looks like when a child delegates, whether the budget bar is past its limit —
is a pure function of events, and a test that had to spin up a display to
check them would be a test nobody runs.

The event vocabulary is `control.events.to_client_event`, shared with the SSE
endpoint. See the package docstring for why that, and not HTTP, is the
contract worth sharing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..control.events import ALERT_EVENTS, to_client_event

# The one colour reserved for a broken chain. Named here and nowhere else so
# a screen cannot borrow it for a warning; `test_tui.py` greps for that.
ALERT_ROLE = "danger"
APPROVAL_ROLE = "warning"


@dataclass
class ToolCall:
    tool: str
    step: int
    preview: str
    status: str = "running"          # running | ok | failed | refused
    task_id: str = ""
    depth: int = 0                   # 0 = this task, 1+ = a delegated child

    def render(self, glyphs: bool = True) -> str:
        mark = {"running": "…", "ok": "✓", "failed": "✗", "refused": "⊘"}
        plain = {"running": "..", "ok": "OK", "failed": "!!", "refused": "NO"}
        m = (mark if glyphs else plain)[self.status]
        indent = ("  " * self.depth) + ("└─ " if self.depth and glyphs
                                        else "-- " if self.depth else "")
        return f"{indent}{m} {self.tool}  {self.preview}"


@dataclass
class Budget:
    spent_usd: float = 0.0
    limit_usd: float = 0.0
    spent_seconds: float = 0.0
    limit_seconds: float = 0.0

    def fraction(self) -> float:
        """The WORST of the two axes, not their average.

        A task at 5% of its money and 99% of its clock is about to stop, and
        a bar showing 52% would be describing neither thing that is true.
        Wall clock is the axis that binds on this hardware anyway.
        """
        fracs = []
        if self.limit_usd > 0:
            fracs.append(self.spent_usd / self.limit_usd)
        if self.limit_seconds > 0:
            fracs.append(self.spent_seconds / self.limit_seconds)
        return max(fracs) if fracs else 0.0

    def render(self) -> str:
        return (f"${self.spent_usd:.4f}/{self.limit_usd:.2f}  "
                f"{self.spent_seconds:.0f}s/{self.limit_seconds:.0f}s  "
                f"{self.fraction() * 100:.0f}%")


@dataclass
class RunState:
    """Everything the five screens display, folded from one event stream."""

    output: list[str] = field(default_factory=list)
    trail: list[ToolCall] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    approvals: list[dict] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    status: str = "pending"
    # True between dispatching a model call and the first token of its reply.
    waiting: bool = False
    _children: set = field(default_factory=set)

    def text(self) -> str:
        return "".join(self.output)

    # -- folding ----------------------------------------------------------

    def apply_live(self, kind: str, data: dict) -> None:
        """Ephemeral events from the in-process bus: tokens and step totals.

        These are NOT on the hash chain and must never be read back to make a
        claim about what happened. They exist so a 40-second CPU turn looks
        like work rather than a hang.
        """
        if kind == "waiting":
            # Prompt evaluation. A fixed ~2.4s on this hardware before the
            # first token, so a surface that only reacts to tokens shows
            # nothing for most of a short turn.
            self.waiting = True
        elif kind == "token":
            self.waiting = False
            self.output.append(data.get("text", ""))
        elif kind == "step":
            self.budget = Budget(
                spent_usd=data.get("spent_usd", 0.0),
                limit_usd=data.get("budget_usd", 0.0),
                spent_seconds=data.get("spent_seconds", 0.0),
                limit_seconds=data.get("budget_seconds", 0.0),
            )

    def apply_event(self, internal: dict) -> bool:
        """One chain event, mapped through the SHARED vocabulary.

        Returns whether it was displayed. `to_client_event` returns None for
        anything not in `CLIENT_EVENTS`, which is how `monitor.*` stays
        invisible to this surface without this file knowing the rule.
        """
        ce = to_client_event(internal)
        if ce is None:
            return False
        name, data = ce["event"], ce["data"]

        if name in ALERT_EVENTS:
            # A SEPARATE list, not a severity field on a shared one. Two
            # collections cannot be accidentally rendered with one style.
            self.alerts.append({"event": name, "data": data,
                                "role": ALERT_ROLE})
            return True

        if name == "tool.called":
            tid = internal.get("task_id", "")
            depth = 1 if tid and tid in self._children else 0
            self.trail.append(ToolCall(
                tool=data.get("tool", "?"), step=data.get("step", 0),
                preview=data.get("preview", ""), task_id=tid, depth=depth))
        elif name == "delegation.opened":
            self._children.add(data.get("child_task_id", ""))
        elif name == "task.status":
            self.status = data.get("status", self.status)
        elif name == "review.requested":
            self.approvals.append({"data": data, "role": APPROVAL_ROLE})
        return True

def fold(events: list[dict], live: list[tuple[str, dict]] | None = None) -> RunState:
    """Build a RunState from a chain history plus any live events."""
    st = RunState()
    for e in events:
        st.apply_event(e)
    for kind, data in (live or []):
        st.apply_live(kind, data)
    return st
