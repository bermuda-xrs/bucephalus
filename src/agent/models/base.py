"""M4.1 — the Backend protocol.

Both backends take `prefix` and `tail` SEPARATELY so the adapter can place the
cache breakpoint correctly. Do not concatenate them in caller code.
"""

from __future__ import annotations

import time
from typing import Callable, Protocol, runtime_checkable

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
        on_chunk: Callable[[str], None] | None = None,
    ) -> ModelResponse: ...
    # `on_chunk` is B5. Optional and keyword-friendly so the fourteen existing
    # call sites are untouched: a surface that wants live output asks for it,
    # and nothing that does not care has to know it exists.

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
        """Tokens/sec, WITHOUT calling the model.

        This used to fire a real completion the first time the router asked.
        Three things wrong with that: the call bypassed `ctx.record`, so it
        never appeared in `calls` and quietly broke invariant 12; on a CPU
        tier it triggered a cold model load (~58s measured) purely to decide
        where to route; and it charged a paid backend for a routing decision.

        The router now prefers rates observed from real work (see
        `Telemetry.observed_tokens_per_sec`) and falls back to the configured
        assumption. Use `probe_throughput()` when you actually want to measure.
        """
        if self._tokens_per_sec is not None:
            return self._tokens_per_sec
        return self.cfg.assumed_tokens_per_sec

    def probe_throughput(self) -> float:
        """Measure for real, by making one small call. Explicit, never implicit.

        Used by `alpha bench-local`. Still not recorded in `calls` — it is a
        benchmark of the backend, not work done for a task — so it is kept out
        of the routing path where that distinction would matter.
        """
        t0 = time.time()
        try:
            resp = self.complete(  # type: ignore[attr-defined]
                prefix="You are a latency probe. Answer in one word.",
                tail="Reply with exactly the word: ready.",
                tools=None,
                max_tokens=16,
                temperature=0.0,
            )
            elapsed = max(1e-3, time.time() - t0)
            self._tokens_per_sec = max(1, resp.usage.output_tokens) / elapsed
        except Exception:  # noqa: BLE001 - an unreachable backend is not fatal
            self._tokens_per_sec = 0.0
        return self._tokens_per_sec

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
