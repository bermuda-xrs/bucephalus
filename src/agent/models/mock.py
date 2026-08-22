"""A scripted backend.

Not decoration: the acceptance tests for M1-M13 must run with no network and
no GPU, or they will not be run. It reports plausible token counts so the
telemetry path is exercised too.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable

from ..config import BackendConfig
from ..kernel.types import ModelResponse, TokenUsage
from ..tokens import count_tokens
from .base import BackendBase

Responder = Callable[[str, str], str]


class MockBackend(BackendBase):
    def __init__(
        self,
        name: str,
        cfg: BackendConfig,
        script: Iterable[str] | None = None,
        responder: Responder | None = None,
    ):
        super().__init__(name, cfg)
        self._script = list(script or [])
        self._i = 0
        self._responder = responder
        self.calls: list[dict] = []
        self.prompts: list[str] = []

    def complete(
        self,
        prefix: str,
        tail: str,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        schema: dict | None = None,
    ) -> ModelResponse:
        t0 = time.time()
        self.prompts.append(prefix + "\n" + tail)
        self.calls.append({"prefix": prefix, "tail": tail, "max_tokens": max_tokens})

        if self._responder is not None:
            text = self._responder(prefix, tail)
        elif self._i < len(self._script):
            text = self._script[self._i]
            self._i += 1
        elif self._script:
            text = self._script[-1]
        else:
            text = '<final>{"answer": "mock"}</final>'

        in_tok = count_tokens(prefix) + count_tokens(tail)
        # Pretend the prefix cached after the first call, so cache metrics move.
        cached = count_tokens(prefix) if len(self.calls) > 1 else 0
        usage = TokenUsage(
            input_tokens=in_tok - cached,
            output_tokens=count_tokens(text),
            cache_read_tokens=cached,
            cache_write_tokens=count_tokens(prefix) if len(self.calls) == 1 else 0,
        )
        return self._response(text, usage, t0, {"mock": True})

    # measure_throughput is deliberately NOT overridden. It used to return a
    # hardcoded 500 tok/s, which quietly hid the router's real fallback path
    # from every test that used a mock backend.

    def queue(self, *texts: str) -> None:
        self._script.extend(texts)
