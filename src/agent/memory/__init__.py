"""A1 — persistent memory, in three tiers.

    tier 1   USER.md + MEMORY.md, always in the stable prefix   size-capped
    tier 2   the FTS5 session index from Phase 2                unbounded
    tier 3   an external provider interface                     stub only

TIER 1 IS IN THE PROMPT. That single fact drives every design decision here,
because it means a memory entry is not data the agent reads — it is text the
agent is, on every future turn, in every future session.

Two consequences, both enforced rather than documented:

**The prefix cannot move mid-task.** Tier 1 is snapshotted at task start, the
same way skills are frozen by `freeze_skills`. A write during a turn lands on
disk immediately and is not visible until the next session or compression
boundary. That is correct behaviour and not a limitation: a file on disk is
not the context of the current turn. Because the builder holds a snapshot,
PrefixGuard cannot trip and the cache hit ratio cannot move — the prefix bytes
are identical by construction, not by discipline.

**Untrusted content cannot reach tier 1.** `memory_write` carries the same
`max_arg_taint=USER` ceiling as `write_file`, so content derived from a
webpage is refused by the existing capability gate before it reaches this
module. See `docs/SPEC-DELTA.md` C32 for why refusal rather than the approval
queue the brief asked for.

Tier 2 is where untrusted-derived content legitimately lives: it is retrieved
on demand and comes back stamped UNTRUSTED, never silently promoted.
"""

from .store import (
    MemoryEntry,
    MemoryStore,
    MemoryTooLarge,
    UntrustedMemoryRefused,
    tier1_snapshot,
)
from .provider import ExternalMemoryProvider, NoExternalProvider

__all__ = [
    "MemoryEntry",
    "MemoryStore",
    "MemoryTooLarge",
    "UntrustedMemoryRefused",
    "tier1_snapshot",
    "ExternalMemoryProvider",
    "NoExternalProvider",
]
