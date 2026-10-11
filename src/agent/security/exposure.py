from __future__ import annotations

from dataclasses import dataclass, field


class ToolNotExposed(RuntimeError):
    ...


CHILD_DENIED = frozenset({
    "delegate",
    "clarify",
    "memory_write",
    "memory_consolidate",
})


@dataclass(frozen=True)
class ToolPolicy:
    denied: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""
    denied_prefixes: tuple[str, ...] = ()

    def denies(self, tool_name: str) -> bool:
        return tool_name in self.denied or tool_name.startswith(self.denied_prefixes)

    def narrowed_by(self, more: frozenset[str], reason: str) -> "ToolPolicy":
        return ToolPolicy(
            denied=frozenset(self.denied) | frozenset(more),
            reason="; ".join(x for x in (self.reason, reason) if x),
            denied_prefixes=self.denied_prefixes,
        )

    def describe(self) -> dict:
        return {"denied": sorted(self.denied), "reason": self.reason, "denied_prefixes": list(self.denied_prefixes)}


UNRESTRICTED = ToolPolicy()


def child_policy(parent: ToolPolicy) -> ToolPolicy:
    return parent.narrowed_by(CHILD_DENIED, "delegated child")


def check(policy: ToolPolicy, tool_name: str) -> None:
    if policy.denies(tool_name):
        raise ToolNotExposed(
            f"{tool_name} is not available to this agent"
            + (f" ({policy.reason})" if policy.reason else "")
            + ". The tool exists; this agent is not permitted to call it."
        )
