"""Tier 3 — an interface with no implementation, deliberately.

3.5 says interface only, and that is the right call for a reason worth
writing down: every external memory provider on offer is a vector database,
and a vector database would put an embedding model and a similarity threshold
inside the retrieval path of a system whose entire value is that its
measurements can be checked. "The right memory was not retrieved" is already
unmeasured here — `measure_retrieval_precision` has no caller, DEFERRED — and
adding a second unmeasured retrieval stage on top would compound it.

So this file exists to fix the SHAPE of the thing, so that a later
implementation cannot quietly change the contract:

  * retrieval returns candidates, never a decision
  * every candidate carries a taint, and it is UNTRUSTED unless the provider
    can prove otherwise — a remote store is remote input
  * a provider is asked, never trusted: nothing it returns may enter tier 1
    without passing the same gate a webpage would

`NoExternalProvider` is the shipped implementation and returns nothing. It is
not a placeholder to be filled in casually.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..kernel.types import Taint


@runtime_checkable
class ExternalMemoryProvider(Protocol):
    """Tier 3. Implement this, do not widen it."""

    name: str

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        """Candidates as `{"text": str, "taint": Taint, "source": str}`.

        MUST stamp `Taint.UNTRUSTED` on anything it cannot prove the origin
        of. A provider that returns TRUSTED text is claiming the remote store
        cannot be written to by anyone else, which is a claim about somebody
        else's infrastructure.
        """
        ...

    def remember(self, text: str, taint: Taint) -> str:
        """Store and return an id. May refuse."""
        ...


class NoExternalProvider:
    """The shipped tier 3: there isn't one."""

    name = "none"

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        return []

    def remember(self, text: str, taint: Taint) -> str:
        raise NotImplementedError(
            "tier 3 is an interface with no implementation; memory lives in "
            "tier 1 (USER.md/MEMORY.md) and tier 2 (search_history)"
        )
