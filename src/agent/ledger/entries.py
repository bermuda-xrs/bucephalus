from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..kernel.types import Taint
from ..tokens import count_tokens

EntryKind = Literal["observation", "action", "note", "tombstone", "review"]


@dataclass
class LedgerEntry:
    kind: EntryKind
    content: str
    artifact_id: str | None = None
    taint: Taint = Taint.TRUSTED
    pinned: bool = False
    n_tokens: int = 0
    exact_strings: list[str] = field(default_factory=list)
    label: str = ""

    def __post_init__(self) -> None:
        if not self.n_tokens:
            self.n_tokens = count_tokens(self.content)

    @classmethod
    def observation(
        cls,
        content: str,
        artifact_id: str | None = None,
        taint: Taint = Taint.UNTRUSTED,
        label: str = "",
    ) -> LedgerEntry:
        return cls(
            kind="observation",
            content=content,
            artifact_id=artifact_id,
            taint=taint,
            label=label,
        )

    @classmethod
    def action(cls, content: str, label: str = "") -> LedgerEntry:
        return cls(kind="action", content=content, taint=Taint.TRUSTED, label=label)

    @classmethod
    def note(cls, content: str, pinned: bool = False) -> LedgerEntry:
        return cls(kind="note", content=content, taint=Taint.TRUSTED, pinned=pinned)

    def render(self) -> str:
        prefix = {
            "observation": "OBSERVATION",
            "action": "ACTION",
            "note": "NOTE",
            "tombstone": "DROPPED",
            "review": "REVIEW",
        }[self.kind]
        tag = f" {self.label}" if self.label else ""
        taint = f" taint={self.taint.value}" if self.kind == "observation" else ""
        return f"<{prefix.lower()}{tag}{taint}>\n{self.content}\n</{prefix.lower()}>"


def render_tail(entries: list[LedgerEntry]) -> str:
    return "\n\n".join(e.render() for e in entries)


def sum_tokens(entries: list[LedgerEntry]) -> int:
    return sum(e.n_tokens for e in entries)
