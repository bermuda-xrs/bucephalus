"""B3 — "the tool exists" is not "this agent may call it".

Until now every agent saw one registry and the capability gate was the only
thing between a model and any tool in it. That is fine for a single agent and
wrong the moment work is delegated: a child opened to summarise a document
could call `git_push`, and nothing but the gate's taint arithmetic stood in
the way.

TWO DESIGN CHOICES WORTH DEFENDING.

**Refuse at dispatch; do not hide from the manual.** A tool omitted from the
manual and a tool refused when called produce different behaviour, and only
the second is auditable. An agent that cannot see a tool works around its
absence silently; an agent that is refused leaves a record saying what it
tried. The refusal is the evidence, so the tool stays visible and the call
fails. It also keeps the prefix identical between parent and child, which
matters for the same reason it mattered in Phase 3.

**Restrictions compose by UNION of denials, never by replacement.** A child
is denied everything its parent was denied, plus its own restrictions. Stated
as allow-sets that is an intersection, and the direction is the whole point:
a delegated agent can never reach a tool its parent could not, so a chain of
delegations can only narrow. Any other rule lets an agent widen its own
authority by delegating to itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ToolNotExposed(RuntimeError):
    """The tool exists and this agent may not call it."""


# What a delegated child loses by default. Each is here for a stated reason,
# not for tidiness.
#
#   delegate            leaves cannot spawn. Unbounded delegation depth is
#                       how a coordination failure becomes a fork bomb, and
#                       the evidence on multi-agent systems is discouraging
#                       enough at ONE level (41-87% failure rates across
#                       1,600+ runs) without recursion.
#   clarify             a child cannot ask the user questions. The human
#                       approved a parent task, not a conversation with a
#                       subagent they never opened.
#   memory_write        tier 1 is in the prefix of every future session. A
#   memory_consolidate  child opened to read one document has no business
#                       editing what the agent believes forever.
#
# `delegate` and `clarify` have no tool yet. They are named here anyway,
# because the policy is the durable artifact: a tool added later inherits the
# restriction instead of needing someone to remember it.
CHILD_DENIED = frozenset({
    "delegate",
    "clarify",
    "memory_write",
    "memory_consolidate",
})


@dataclass(frozen=True)
class ToolPolicy:
    """What one agent may call. Empty denials means unrestricted."""

    denied: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""

    def denies(self, tool_name: str) -> bool:
        return tool_name in self.denied

    def narrowed_by(self, more: frozenset[str], reason: str) -> "ToolPolicy":
        """A child policy. UNION of denials — never a replacement.

        `narrowed_by` rather than `with_denied` because the name should make
        the only legal direction obvious at the call site.
        """
        return ToolPolicy(
            denied=frozenset(self.denied) | frozenset(more),
            reason="; ".join(x for x in (self.reason, reason) if x),
        )

    def describe(self) -> dict:
        return {"denied": sorted(self.denied), "reason": self.reason}


UNRESTRICTED = ToolPolicy()


def child_policy(parent: ToolPolicy) -> ToolPolicy:
    return parent.narrowed_by(CHILD_DENIED, "delegated child")


def check(policy: ToolPolicy, tool_name: str) -> None:
    """Raise if this agent may not call this tool."""
    if policy.denies(tool_name):
        raise ToolNotExposed(
            f"{tool_name} is not available to this agent"
            + (f" ({policy.reason})" if policy.reason else "")
            + ". The tool exists; this agent is not permitted to call it."
        )
