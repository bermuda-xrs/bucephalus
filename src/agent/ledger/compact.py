"""M3.3 / M3.4 — compaction by deletion, and the hard context ceiling.

Three rules, each contradicting an obvious-seeming approach:

  1. Deletion, not summarization. Drop whole low-value entries; keep survivors
     verbatim.
  2. NEVER token-prune a trajectory. A 2026 study found all 17 tested
     configurations collapsed to near-zero task success while achieving
     genuine compression — the removed tokens carry the action structure.
     Compress documents if you like. Never the agent's own trail.
  3. Preserve exact strings character-for-character.
"""

from __future__ import annotations

import re

from .entries import LedgerEntry

EXACT_PATTERNS = [
    r"[A-Za-z]:[\\/][^\s\"']+",  # windows paths
    # posix paths, absolute OR relative — a relative path clipped to its last
    # segment is not "character-for-character", which is the whole rule
    r"(?:\.{1,2}/)?(?:[\w.\-]+/)+[\w.\-]+",
    r"\b[\w.]+Error\b[^\n]*",  # errors
    r"\bline \d+\b",  # line numbers
    r"\b[0-9a-f]{7,40}\b",  # hashes
    r"\bv?\d+\.\d+\.\d+\b",  # versions
    r"\b\w+\.(?:py|md|json|toml|yaml|yml|txt|sql|ts|js)\b",  # filenames
]

_COMPILED = [re.compile(p) for p in EXACT_PATTERNS]


def extract_exact_strings(content: str, limit: int = 12) -> list[str]:
    """File paths, error messages, identifiers, versions, line numbers, hashes.

    These must survive character-for-character or the tombstone is useless.
    """
    found: list[str] = []
    seen: set[str] = set()
    for rx in _COMPILED:
        for m in rx.findall(content):
            s = m if isinstance(m, str) else m[0]
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                found.append(s)
            if len(found) >= limit:
                return found
    return found


def tombstone(entry: LedgerEntry, exact: list[str]) -> LedgerEntry:
    """A placeholder preserving id, size, and exact strings. Expandable on request."""
    aid = entry.artifact_id or "inline"
    contains = ", ".join(exact) if exact else "nothing quotable"
    expand = f' · expand_artifact("{aid}")' if entry.artifact_id else ""
    body = (
        f"[artifact {aid} · {entry.kind} · {entry.n_tokens:,} tokens · dropped · "
        f"contains: {contains}{expand}]"
    )
    return LedgerEntry(
        kind="tombstone",
        content=body,
        artifact_id=entry.artifact_id,
        taint=entry.taint,
        pinned=False,
        exact_strings=exact,
        label=entry.label,
    )


# Tombstones were never dropped, so each boundary started from a higher floor:
# ~28 tokens per dropped observation, which at the shipped window put the
# floor alone over the ceiling by step ~60 and the context near the whole 16k
# window by step 400 - compaction firing every step and unable to get under
# (measured 2026-09-25). Past this share of the target, the oldest fold into
# one bounded index.
TOMBSTONE_SHARE = 0.25
INDEXED_IDS = 20
INDEX_LABEL = "tombstone-index"


def _fold_tombstones(entries: list[LedgerEntry], budget: int) -> list[LedgerEntry]:
    """Fold the oldest tombstones into one index entry, keeping the newest
    individually while they fit in half the budget.

    Nothing becomes unreachable: an artifact is never deleted, the index
    names the newest INDEXED_IDS ids and counts the rest, and
    expand_artifact(id) returns any of them in full. What leaves the CONTEXT
    is the older tombstones' quoted exact strings - one expand away, the same
    trade a tombstone made with its observation. The index's taint is the
    JOIN of everything folded into it: folding must not launder provenance.
    """
    from ..kernel.types import join_taint

    tombs = [i for i, e in enumerate(entries) if e.kind == "tombstone"]
    if sum(entries[i].n_tokens for i in tombs) <= budget:
        return entries
    keep, used = set(), 0
    for i in reversed(tombs):
        if entries[i].label == INDEX_LABEL or used + entries[i].n_tokens > budget // 2:
            continue
        keep.add(i)
        used += entries[i].n_tokens
    folded = [i for i in tombs if i not in keep]
    if len(folded) < 2:
        return entries
    ids: list[str] = []
    count = 0
    for i in folded:
        e = entries[i]
        if e.label == INDEX_LABEL:
            count += int(e.exact_strings[0]) if e.exact_strings else 0
            ids.extend(e.exact_strings[1:])
        else:
            count += 1
            if e.artifact_id:
                ids.append(e.artifact_id)
    ids = ids[-INDEXED_IDS:]
    more = count - len(ids)
    body = (f"[{count} earlier observations dropped from context. Newest ids: "
            + (", ".join(reversed(ids)) or "none recorded")
            + (f", and {more} more in this task's record" if more > 0 else "")
            + ". expand_artifact(id) returns any of them in full.]")
    index = LedgerEntry(kind="tombstone", content=body,
                        taint=join_taint(*(entries[i].taint for i in folded)),
                        exact_strings=[str(count), *ids], label=INDEX_LABEL)
    folded_set, first = set(folded), folded[0]
    return [index if i == first else e for i, e in enumerate(entries)
            if i == first or i not in folded_set]


def compact(entries: list[LedgerEntry], target_tokens: int) -> list[LedgerEntry]:
    """Deletion-based. Tombstone whole entries from the tail backward.

    Survivors are untouched — no paraphrase, no token pruning, no rewriting.
    Tombstones past TOMBSTONE_SHARE of the target fold into one index, so the
    floor a long session compacts to is bounded (_fold_tombstones).
    """
    keep: list[LedgerEntry] = []
    running = 0
    for e in reversed(entries):  # newest first
        if e.kind == "tombstone":
            keep.append(e)
            running += e.n_tokens
            continue
        if e.pinned or running + e.n_tokens <= target_tokens:
            keep.append(e)
            running += e.n_tokens
        else:
            exact = extract_exact_strings(e.content)
            t = tombstone(e, exact)
            keep.append(t)
            running += t.n_tokens
    return _fold_tombstones(list(reversed(keep)),
                            max(200, int(target_tokens * TOMBSTONE_SHARE)))


def enforce_ceiling(
    prefix_tokens: int, tail_tokens: int, window: int, fill_ratio: float
) -> str:
    """Exceeding the ceiling is a delegation trigger, not a 'it still fits' situation.

    Long-context degradation arrives in cliffs, not slopes. Effective usable
    context is well below the advertised number.
    """
    ceiling = int(window * fill_ratio)
    if prefix_tokens + tail_tokens <= ceiling:
        return "ok"
    if tail_tokens > ceiling * 0.6:
        return "compact"  # tail is the problem
    return "delegate"  # the work itself is too big — see M10
