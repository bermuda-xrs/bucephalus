from __future__ import annotations

import os
import time

from ..config import BackendConfig
from ..kernel.types import ModelResponse, TokenUsage
from .base import BackendBase, BackendError


class AnthropicBackend(BackendBase):
    def __init__(self, name: str, cfg: BackendConfig, client=None):
        super().__init__(name, cfg)
        self.model = cfg.model
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover
                raise BackendError("pip install anthropic") from e
            if not os.environ.get("ANTHROPIC_API_KEY"):
                pass
            self._client = anthropic.Anthropic()
        return self._client

    def complete(
        self,
        prefix: str,
        tail: str,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        schema: dict | None = None,
        on_chunk=None,
    ) -> ModelResponse:
        t0 = time.time()

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max(max_tokens, self.cfg.max_output_tokens),
            "system": [
                {
                    "type": "text",
                    "text": prefix,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": tail}],
        }
        if tools:
            kwargs["tools"] = tools
        if self.cfg.accepts_temperature:
            kwargs["temperature"] = temperature

        output_config: dict = {"effort": self.cfg.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs["output_config"] = output_config

        if self.cfg.thinking == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        elif self.cfg.effort in ("low", "medium", "high"):
            kwargs["thinking"] = {"type": "disabled"}

        with self.client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                if on_chunk is not None and text:
                    on_chunk(text)
            resp = stream.get_final_message()

        if getattr(resp, "stop_reason", None) == "refusal":
            details = getattr(resp, "stop_details", None)
            cat = getattr(details, "category", None) if details else None
            raise BackendError(f"model declined the request (category={cat})")

        usage = TokenUsage(
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        )
        return self._response(
            extract_text(resp),
            usage,
            t0,
            {"stop_reason": getattr(resp, "stop_reason", None),
             "model": getattr(resp, "model", self.model)},
        )


def extract_text(resp) -> str:
    out = []
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "text":
            out.append(block.text)
    return "".join(out)
