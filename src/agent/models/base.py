"""M4.1 — the Backend protocol.

Both backends take `prefix` and `tail` SEPARATELY so the adapter can place the
cache breakpoint correctly. Do not concatenate them in caller code.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from ..config import BackendConfig
from ..kernel.types import ModelResponse, TokenUsage


@runtime_checkable
class Backend(Protocol):
    name: str
    cost_vector: dict
    cfg: BackendConfig

    def complete(
        self,
        prefix: str,
        tail: str,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        schema: dict | None = None,
    ) -> ModelResponse: ...

    def measure_throughput(self) -> float: ...


class BackendBase:
    """Shared plumbing. Throughput is measured, never configured."""

    def __init__(self, name: str, cfg: BackendConfig):
        self.name = name
        self.cfg = cfg
        self.cost_vector = cfg.cost_vector()
        self._tokens_per_sec: float | None = None

    @property
    def trust_tier(self) -> str:
        return self.cfg.trust_tier

    @property
    def context_window(self) -> int:
        return self.cfg.budgeted_window

    def default_max_tokens(self) -> int:
        return self.cfg.max_output_tokens

    def measure_throughput(self) -> float:
        """Observed tokens/sec.

        Measured at startup because it varies wildly with quantization, context
        length, and thermal throttling. Cached for the process lifetime.
        """
        if self._tokens_per_sec is not None:
            return self._tokens_per_sec
        probe = "Reply with exactly the word: ready."
        t0 = time.time()
        try:
            resp = self.complete(  # type: ignore[attr-defined]
                prefix="You are a latency probe. Answer in one word.",
                tail=probe,
                tools=None,
                max_tokens=16,
                temperature=0.0,
            )
            elapsed = max(1e-3, time.time() - t0)
            out = max(1, resp.usage.output_tokens)
            self._tokens_per_sec = out / elapsed
        except Exception:  # noqa: BLE001 - an unreachable backend is not fatal here
            self._tokens_per_sec = 0.0
        return self._tokens_per_sec

    def set_throughput(self, tps: float) -> None:
        self._tokens_per_sec = tps

    def _usage(self, **kw: int) -> TokenUsage:
        return TokenUsage(**kw)

    def _response(self, text: str, usage: TokenUsage, t0: float, raw: dict) -> ModelResponse:
        return ModelResponse(
            text=text,
            usage=usage,
            backend=self.name,
            wall_ms=int((time.time() - t0) * 1000),
            raw=raw,
        )


class BackendError(RuntimeError):
    pass
