"""M4.2 — the Anthropic adapter.

    ┌──────────────────────────────────────────────────────────────────┐
    │  UNVERIFIED. This adapter has NEVER been exercised against the   │
    │  live API. It is written and unit-tested against a mock; not one │
    │  request has left this machine.                                  │
    │                                                                  │
    │  Do not describe it as working. Do not publish a number produced │
    │  through it without first making a real call and saying so.      │
    │  The request shape below follows the current API contract as     │
    │  documented, which is a different claim from "this works".       │
    │                                                                  │
    │  To verify: set ANTHROPIC_API_KEY, run one trivial task with     │
    │  --profile mixed, and confirm the temperature omission and the   │
    │  thinking/effort handling are accepted rather than 400'd.        │
    └──────────────────────────────────────────────────────────────────┘


The cache breakpoint goes on the last system block, and nothing else. The tail
goes in the user turn, after it.

Two API facts that the build spec predates and that will 400 if ignored:
  * `temperature` is rejected on Opus 5 / Sonnet 5 / Opus 4.7+. The adapter
    omits it unless the backend config says the model accepts it.
  * thinking is configured as {"type": "adaptive"} with depth controlled by
    output_config.effort. `budget_tokens` is gone.
"""

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
                # The SDK also resolves `ant auth login` profiles; only warn.
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
    ) -> ModelResponse:
        t0 = time.time()

        kwargs: dict = {
            "model": self.model,
            "max_tokens": max(max_tokens, self.cfg.max_output_tokens),
            "system": [
                {
                    "type": "text",
                    "text": prefix,
                    "cache_control": {"type": "ephemeral"},  # breakpoint HERE
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
            # disabling thinking is rejected above `high` effort
            kwargs["thinking"] = {"type": "disabled"}

        resp = self.client.messages.create(**kwargs)

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
    """Text blocks only. Thinking blocks are not the answer."""
    out = []
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "text":
            out.append(block.text)
    return "".join(out)
