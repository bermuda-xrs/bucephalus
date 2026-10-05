from __future__ import annotations

from dataclasses import dataclass, field

from ..control.events import ALERT_EVENTS, to_client_event

ALERT_ROLE = "danger"
APPROVAL_ROLE = "warning"


@dataclass
class ToolCall:
    tool: str
    step: int
    preview: str
    status: str = "running"
    task_id: str = ""
    depth: int = 0

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
    output: list[str] = field(default_factory=list)
    trail: list[ToolCall] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    approvals: list[dict] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    status: str = "pending"
    waiting: bool = False
    _children: set = field(default_factory=set)

    def text(self) -> str:
        return "".join(self.output)

    def apply_live(self, kind: str, data: dict) -> None:
        if kind == "waiting":
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
        ce = to_client_event(internal)
        if ce is None:
            return False
        name, data = ce["event"], ce["data"]

        if name in ALERT_EVENTS:
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
    st = RunState()
    for e in events:
        st.apply_event(e)
    for kind, data in (live or []):
        st.apply_live(kind, data)
    return st
