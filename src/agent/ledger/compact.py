from __future__ import annotations

import re

from .entries import LedgerEntry

EXACT_PATTERNS = [
    r"[A-Za-z]:[\\/][^\s\"']+",
    r"(?:\.{1,2}/)?(?:[\w.\-]+/)+[\w.\-]+",
    r"\b[\w.]+Error\b[^\n]*",
    r"\bline \d+\b",
    r"\b[0-9a-f]{7,40}\b",
    r"\bv?\d+\.\d+\.\d+\b",
    r"\b\w+\.(?:py|md|json|toml|yaml|yml|txt|sql|ts|js)\b",
]

_COMPILED = [re.compile(p) for p in EXACT_PATTERNS]
ID_RX = re.compile(r"\b[A-Z][A-Z0-9]+-\d{2,}\b")
WORD_RX = re.compile(r"[a-z]{4,}")
STOP = frozenset("with from that this have into each their what which about only answer json file work task report "
                 "find read write when then them they there these those your".split())
EVIDENCE_MAX = 800
EVIDENCE_HEAD = "Evidence kept verbatim from observations that compaction dropped:"


def evidence_lines(text: str, objective: str) -> list[str]:
    terms = {w for w in WORD_RX.findall(objective.lower()) if w not in STOP}
    ids, overlaps = [], []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if ID_RX.search(s):
            ids.append(s[:240])
        elif len(terms & set(WORD_RX.findall(s.lower()))) >= 2:
            overlaps.append(s[:240])
    return ids + overlaps


def _merge_evidence(old: list[str], new: list[str]) -> list[str]:
    merged = list(dict.fromkeys(old + new))
    while merged and sum(len(x) + 1 for x in merged) > EVIDENCE_MAX:
        merged.pop(0)
    return merged


def extract_exact_strings(content: str, limit: int = 12) -> list[str]:
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


TOMBSTONE_SHARE = 0.25
INDEXED_IDS = 20
INDEX_LABEL = "tombstone-index"


def _fold_tombstones(entries: list[LedgerEntry], budget: int) -> list[LedgerEntry]:
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


def compact(entries: list[LedgerEntry], target_tokens: int, objective: str | None = None) -> list[LedgerEntry]:
    from ..kernel.types import join_taint

    keep: list[LedgerEntry] = []
    running = 0
    pins: list[str] = []
    sources = []
    for e in reversed(entries):
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
            if objective and e.kind == "observation":
                pins = evidence_lines(e.content, objective) + pins
                sources.append(e.taint)
    out = _fold_tombstones(list(reversed(keep)), max(200, int(target_tokens * TOMBSTONE_SHARE)))
    if not pins:
        return out
    old = next((e for e in out if e.label == "evidence"), None)
    lines = _merge_evidence(old.content.split(chr(10))[1:] if old else [], pins)
    note = LedgerEntry.observation(EVIDENCE_HEAD + chr(10) + chr(10).join(lines),
                                   taint=join_taint(*sources, *( [old.taint] if old else [])), label="evidence")
    note.pinned = True
    return [e for e in out if e.label != "evidence"] + [note]


def enforce_ceiling(
    prefix_tokens: int, tail_tokens: int, window: int, fill_ratio: float
) -> str:
    ceiling = int(window * fill_ratio)
    if prefix_tokens + tail_tokens <= ceiling:
        return "ok"
    if tail_tokens > ceiling * 0.6:
        return "compact"
    return "delegate"
