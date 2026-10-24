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

    def measure_throughput(self) -> float: ...


class BackendBase:
    def __init__(self, name: str, cfg: BackendConfig):
        self.name = name
        self.cfg = cfg
        self.cost_vector = cfg.cost_vector()

    @property
    def trust_tier(self) -> str:
        return self.cfg.trust_tier

    @property
    def context_window(self) -> int:
        return self.cfg.budgeted_window

    def default_max_tokens(self) -> int:
        return self.cfg.max_output_tokens

    def measure_throughput(self) -> float:
        return self.cfg.assumed_tokens_per_sec

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
