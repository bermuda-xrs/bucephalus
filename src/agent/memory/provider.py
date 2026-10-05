from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..kernel.types import Taint


@runtime_checkable
class ExternalMemoryProvider(Protocol):
    name: str

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        ...

    def remember(self, text: str, taint: Taint) -> str:
        ...


class NoExternalProvider:
    name = "none"

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        return []

    def remember(self, text: str, taint: Taint) -> str:
        raise NotImplementedError(
            "tier 3 is an interface with no implementation; memory lives in "
            "tier 1 (USER.md/MEMORY.md) and tier 2 (search_history)"
        )
