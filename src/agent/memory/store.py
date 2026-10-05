from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..kernel.types import Taint

USER_CAP = 1400
MEMORY_CAP = 2200

USER_FILE = "USER.md"
MEMORY_FILE = "MEMORY.md"

_ENTRY_RX = re.compile(r"^- \[(trusted|user|untrusted)\] (.*)$")


class MemoryTooLarge(RuntimeError):
    ...


class UntrustedMemoryRefused(RuntimeError):
    ...


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
        before = self.entries(tier)
        kept, dropped = self._consolidate(before, self.cap(tier))
        self.path(tier).write_text(_render(kept), encoding="utf-8")
        return {"tier": tier, "before": len(before), "after": len(kept),
                "dropped": [d.render() for d in dropped],
                "chars": len(_render(kept)), "cap": self.cap(tier)}

    @staticmethod
    def _consolidate(entries: list[MemoryEntry],
                     cap: int) -> tuple[list[MemoryEntry], list[MemoryEntry]]:
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
        h = hashlib.sha256()
        for tier in sorted(self.FILES):
            h.update(tier.encode())
            h.update(b"\0")
            h.update(self.read_raw(tier).encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()

    def snapshot(self) -> dict:
        return {tier: self.read_raw(tier).rstrip() for tier in sorted(self.FILES)}


BLESSED_FILE = ".fingerprint"

INTACT = "intact"
TAMPERED = "tampered"
UNRECORDED = "unrecorded"


def forget(store: MemoryStore, text: str) -> list[str]:
    needle = text.lower()
    removed = []
    for tier in sorted(store.FILES):
        entries = store.entries(tier)
        gone = [e for e in entries if needle in e.text.lower()]
        if gone:
            store.path(tier).write_text(_render([e for e in entries if e not in gone]),
                                        encoding="utf-8")
            removed += [f"{tier}: {e.text}" for e in gone]
    record_fingerprint(store)
    return removed


def record_fingerprint(store: MemoryStore) -> str:
    fp = store.fingerprint()
    (store.root / BLESSED_FILE).write_text(fp, encoding="utf-8")
    return fp


def check_fingerprint(store: MemoryStore) -> str:
    p = store.root / BLESSED_FILE
    recorded = p.read_text(encoding="utf-8").strip() if p.exists() else None
    if not recorded:
        empty = all(not store.read_raw(t).strip() for t in store.FILES)
        return INTACT if empty else UNRECORDED
    return INTACT if recorded == store.fingerprint() else TAMPERED


def tier1_snapshot(store: MemoryStore | None, isolated: bool) -> dict:
    if store is None or isolated:
        return {tier: "" for tier in sorted(MemoryStore.FILES)}
    return store.snapshot()


def render_for_prefix(snapshot: dict) -> str:
    parts = []
    for tier in sorted(snapshot):
        body = (snapshot.get(tier) or "").rstrip()
        parts.append(f"<memory name=\"{tier}\">\n{body}\n</memory>\n")
    return "".join(parts)
