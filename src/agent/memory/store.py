"""Tier 1: two markdown files in the user data dir, covered by the chain.

WHY MARKDOWN AND NOT A TABLE. Tier 1 goes into the prompt verbatim, so what is
on disk should be what the model sees. A schema would mean a rendering step
between storage and prompt, and a rendering step is somewhere for the bytes to
change without anyone editing anything — which is the one thing the stable
prefix cannot tolerate.

Each entry is one line:

    - [user] the user prefers metric units

The tag is the taint of the content it derived from. Tier 1 cannot hold
`untrusted` (the gate refuses it upstream), but the tag is still recorded,
because provenance that is only enforced at the boundary is provenance that
disappears the moment the boundary moves.

THE CAP IS A CAP, NOT A SUGGESTION. Exceeding it triggers consolidation, never
truncation: entries are de-duplicated first, and only if that is not enough
are the oldest evicted — each eviction appended to the hash chain, so nothing
leaves tier 1 without a record. Model-assisted summarisation would consolidate
better and is deliberately not here; it would put a nondeterministic call
inside the write path of the thing the prefix is built from.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..kernel.types import Taint

# Caps in characters, from the design that is known to work without a vector
# database. Small on purpose: this text is paid for on every single turn.
USER_CAP = 1400
MEMORY_CAP = 2200

USER_FILE = "USER.md"
MEMORY_FILE = "MEMORY.md"

_ENTRY_RX = re.compile(r"^- \[(trusted|user|untrusted)\] (.*)$")


class MemoryTooLarge(RuntimeError):
    """A single entry cannot fit the tier even with an empty file."""


class UntrustedMemoryRefused(RuntimeError):
    """Untrusted-derived content was offered to tier 1."""


@dataclass(frozen=True)
class MemoryEntry:
    text: str
    taint: Taint

    def render(self) -> str:
        return f"- [{self.taint.value}] {self.text}"


def _parse(text: str) -> list[MemoryEntry]:
    out = []
    for line in text.splitlines():
        m = _ENTRY_RX.match(line.strip())
        if m:
            out.append(MemoryEntry(m.group(2).strip(), Taint(m.group(1))))
    return out


def _render(entries: list[MemoryEntry]) -> str:
    return "\n".join(e.render() for e in entries) + ("\n" if entries else "")


class MemoryStore:
    """Tier 1. One instance per context; the files are the source of truth."""

    FILES = {"user": (USER_FILE, USER_CAP), "memory": (MEMORY_FILE, MEMORY_CAP)}

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, tier: str) -> Path:
        if tier not in self.FILES:
            raise KeyError(f"unknown memory file {tier!r}; expected one of "
                           f"{sorted(self.FILES)}")
        return self.root / self.FILES[tier][0]

    def cap(self, tier: str) -> int:
        return self.FILES[tier][1]

    def read_raw(self, tier: str) -> str:
        p = self.path(tier)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def entries(self, tier: str) -> list[MemoryEntry]:
        return _parse(self.read_raw(tier))

    def write(self, tier: str, text: str, taint: Taint) -> dict:
        """Append one entry. Refuses untrusted content outright.

        The refusal is belt-and-braces: the capability gate already stops an
        untrusted argument reaching `memory_write`, and this is the second
        wall in case a future caller reaches the store directly. A control
        that exists only at the boundary stops existing when someone adds a
        second boundary.
        """
        text = " ".join((text or "").split())
        if not text:
            raise ValueError("refusing to store an empty memory entry")
        if taint == Taint.UNTRUSTED:
            raise UntrustedMemoryRefused(
                "tier 1 memory is rendered into the stable prefix of every "
                "future session, so untrusted-derived content may not enter "
                "it. Tier 2 (search_history) holds it instead and returns it "
                "stamped UNTRUSTED."
            )
        entry = MemoryEntry(text, taint)
        if len(entry.render()) + 1 > self.cap(tier):
            raise MemoryTooLarge(
                f"a single entry of {len(entry.render())} chars cannot fit "
                f"the {tier} cap of {self.cap(tier)}")

        existing = self.entries(tier)
        existing.append(entry)
        kept, dropped = self._consolidate(existing, self.cap(tier))
        self.path(tier).write_text(_render(kept), encoding="utf-8")
        return {
            "tier": tier,
            "stored": entry.render(),
            "entries": len(kept),
            "chars": len(_render(kept)),
            "cap": self.cap(tier),
            "consolidated": [d.render() for d in dropped],
        }

    def consolidate(self, tier: str) -> dict:
        """Explicit consolidation, for `memory_consolidate`."""
        before = self.entries(tier)
        kept, dropped = self._consolidate(before, self.cap(tier))
        self.path(tier).write_text(_render(kept), encoding="utf-8")
        return {"tier": tier, "before": len(before), "after": len(kept),
                "dropped": [d.render() for d in dropped],
                "chars": len(_render(kept)), "cap": self.cap(tier)}

    @staticmethod
    def _consolidate(entries: list[MemoryEntry],
                     cap: int) -> tuple[list[MemoryEntry], list[MemoryEntry]]:
        """De-duplicate, then evict oldest-first only if still over cap.

        Order of operations matters. Evicting first would throw away a fact to
        make room for its own duplicate.
        """
        seen: set[str] = set()
        deduped: list[MemoryEntry] = []
        dropped: list[MemoryEntry] = []
        for e in entries:
            k = e.text.lower()
            if k in seen:
                dropped.append(e)
                continue
            seen.add(k)
            deduped.append(e)
        while deduped and len(_render(deduped)) > cap:
            dropped.append(deduped.pop(0))
        return deduped, dropped

    def fingerprint(self) -> str:
        """One hash over both files, for the integrity chain.

        Covers the FILES, not the parsed entries: a tampered line that fails
        to parse would otherwise be invisible to verification, which is
        exactly the shape an attacker would choose.
        """
        h = hashlib.sha256()
        # Only the memory FILES. The blessing sidecar is deliberately not
        # hashed: including it would mean writing the blessing changes the
        # thing it blesses, and no fingerprint could ever match.
        for tier in sorted(self.FILES):
            h.update(tier.encode())
            h.update(b"\0")
            h.update(self.read_raw(tier).encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()

    def snapshot(self) -> dict:
        """What goes into the prefix. Taken once, at task start."""
        return {tier: self.read_raw(tier).rstrip() for tier in sorted(self.FILES)}


# Tier 1's last known-good fingerprint, beside the memory it describes.
#
# NOT in the run's anchor, which was the first attempt and was wrong: the
# anchor path is per config, memory lives in the user data dir and is shared,
# so running config.local.yaml and then config.openai.yaml would have each
# flagged the other's perfectly good memory as tampered. Caught by the
# cross-session test, which is what that test is for.
#
# WHAT THIS DETECTS, STATED HONESTLY. Unaccounted CHANGE, not authenticated
# tampering. Anyone with write access to the memory directory can edit this
# file too, so it is no defence against an attacker already inside the user
# data dir — at that point no local file helps. What it does catch is the
# realistic set: the agent reaching the files by some path other than the
# write path, a buggy tool, a sync conflict, an editor.
#
# The hash chain is EVIDENCE for a later audit, not a check. Every
# `memory.written` event carries the fingerprint at the time of the write, and
# that record cannot be rewritten without breaking the chain - so after the
# fact, a reader can ask which states this database's agent actually wrote.
# Nothing compares against it at load, deliberately (AUDIT F6, D44): memory is
# one store per user, shared by every config, while each config's chain is its
# own database, and `alpha memory --accept` blesses a state with no chain
# event at all. A load-time comparison would read another config's legitimate
# write, or an accepted state, as tampering - DR1 width, the defect the
# per-config anchor was withdrawn for. `tests/test_memory_integrity_claim.py`
# holds that. So this file is the check, the chain is the record, and neither
# stops a process that can already write the user data directory.
BLESSED_FILE = ".fingerprint"

INTACT = "intact"
TAMPERED = "tampered"
UNRECORDED = "unrecorded"


def record_fingerprint(store: MemoryStore) -> str:
    """Bless the current state. Called after every AUTHORISED mutation.

    Authorised means `memory_write`, `memory_consolidate`, and
    `alpha memory --forget/--accept`. A change that does not come through one
    of those leaves the recorded hash behind, which is the whole detection.
    """
    fp = store.fingerprint()
    (store.root / BLESSED_FILE).write_text(fp, encoding="utf-8")
    return fp


def check_fingerprint(store: MemoryStore) -> str:
    """INTACT, TAMPERED, or UNRECORDED.

    UNRECORDED with EMPTY files is the ordinary first-run case and is fine.
    UNRECORDED with content present is not: memory that exists and was never
    blessed either predates this check or was placed there by something that
    did not go through the write path. Both are treated as tampering, because
    "I cannot account for this text" is the same operational fact as "this
    text was changed" — and either way it goes into every future prompt.
    """
    p = store.root / BLESSED_FILE
    recorded = p.read_text(encoding="utf-8").strip() if p.exists() else None
    if not recorded:
        empty = all(not store.read_raw(t).strip() for t in store.FILES)
        return INTACT if empty else UNRECORDED
    return INTACT if recorded == store.fingerprint() else TAMPERED


def tier1_snapshot(store: MemoryStore | None, isolated: bool) -> dict:
    """The prefix's view of memory, and the one place a benchmark turns it off.

    `isolated` is `config.isolate_retrieval`. A measurement run gets an EMPTY
    snapshot rather than no memory section at all, so the prefix keeps the
    same shape — the tool manual and section headers are identical, and only
    the content differs. Removing the section would change the prefix bytes
    between measured and shipped, and this project measured that prefix
    structure moves pass rates.

    It is also the contamination control: a fact written by the control arm
    would otherwise be in the skill arm's prompt before the model does
    anything at all.
    """
    if store is None or isolated:
        return {tier: "" for tier in sorted(MemoryStore.FILES)}
    return store.snapshot()


def render_for_prefix(snapshot: dict) -> str:
    """Deterministic. Sorted keys, no timestamps, no set iteration."""
    parts = []
    for tier in sorted(snapshot):
        body = (snapshot.get(tier) or "").rstrip()
        parts.append(f"<memory name=\"{tier}\">\n{body}\n</memory>\n")
    return "".join(parts)

