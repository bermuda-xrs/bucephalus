"""M4.3 / M5.4 — the Ollama adapter.

Ollama keeps the prompt in its own KV cache; a stable prefix still helps
latency even though it costs nothing.

Constrained output matters most here, where format adherence is weakest. Pass
a JSON Schema through `format` rather than parsing-and-retrying — every
retry-for-format is pure wasted tokens.
"""

from __future__ import annotations

import time

import httpx

from ..config import BackendConfig
from ..kernel.types import ModelResponse, TokenUsage
from .base import BackendBase, BackendError


class OllamaBackend(BackendBase):
    def __init__(self, name: str, cfg: BackendConfig, client: httpx.Client | None = None):
        super().__init__(name, cfg)
        self.model = cfg.model
        self.endpoint = (cfg.endpoint or "http://localhost:11434").rstrip("/")
        self._client = client

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
        options: dict = {
            "temperature": temperature,
            "num_predict": min(max_tokens, self.cfg.max_output_tokens),
        }
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prefix},
                {"role": "user", "content": tail},
            ],
            "stream": False,
            "options": options,
            # Thinking is a routing variable, not a model setting (M6.2).
            "think": self.cfg.thinking == "adaptive",
        }
        if schema:
            body["format"] = schema
        if tools:
            body["tools"] = tools

        client = self._client or httpx
        try:
            r = client.post(f"{self.endpoint}/api/chat", timeout=600.0, json=body)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            raise BackendError(f"ollama call failed at {self.endpoint}: {e}") from e

        d = r.json()
        message = d.get("message", {}) or {}
        text = message.get("content", "") or ""
        return self._response(
            text,
            TokenUsage(
                input_tokens=d.get("prompt_eval_count", 0) or 0,
                output_tokens=d.get("eval_count", 0) or 0,
            ),
            t0,
            {"done_reason": d.get("done_reason")},
        )

    def available(self) -> bool:
        try:
            client = self._client or httpx
            r = client.get(f"{self.endpoint}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False
