"""M4.3 / M5.4 — the Ollama adapter, native `/api/chat` protocol.

Ollama keeps the prompt in its own KV cache; a stable prefix still helps
latency even though it costs nothing.

Constrained output matters most here, where format adherence is weakest. Pass
a JSON Schema through `format` rather than parsing-and-retrying — every
retry-for-format is pure wasted tokens.

Stall detection now lives in `models/stall.py` so the OpenAI-compatible
adapter shares one rule with this one. The names are re-exported here because
`bench/noise.py` and `execute/loop.py` import them from this module, and a
machine-fault path is the wrong place to churn import lines for tidiness.
See `docs/SPEC-DELTA.md` C28 for the measured parity difference between this
adapter and the same server's `/v1` endpoint.
"""

from __future__ import annotations

import json
import time

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

from ..config import BackendConfig
from ..kernel.types import ModelResponse, TokenUsage
from .base import BackendBase, BackendError
from .stall import (
    ModelStall,
    StallWatch,
    injected_stall,
    injecting_timeout,
    next_call_index,
    reset_rates,  # noqa: F401 - re-exported; bench/noise.py imports it here
)


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
        on_chunk=None,
    ) -> ModelResponse:
        t0 = time.time()
        options: dict = {
            "temperature": temperature,
            "num_predict": min(max_tokens, self.cfg.max_output_tokens),
            # MUST be sent explicitly. Ollama defaults to a 4096-token window
            # regardless of what the model supports, and silently truncates
            # anything longer — no error, just a shorter prompt than the one
            # you built. M3.4 budgets against usable_context_window, so if that
            # is larger than num_ctx the ceiling math is a lie and the agent
            # loses the head of its own context mid-task.
            "num_ctx": self.cfg.budgeted_window,
        }
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prefix},
                {"role": "user", "content": tail},
            ],
            "options": options,
            # Keeping weights resident is the single biggest wall-clock win on
            # a CPU tier: a cold load measured 30s against 8s warm for the same
            # call. Free in money, enormous in the budget that actually binds.
            "keep_alive": self.cfg.keep_alive,
            # Thinking is a routing variable, not a model setting (M6.2).
            "think": self.cfg.thinking == "adaptive",
        }
        if schema:
            body["format"] = schema
        if tools:
            body["tools"] = tools

        body["stream"] = True
        watch = StallWatch(f"{self.endpoint}|{self.model}")

        n = next_call_index()
        injected = injected_stall(n, watch.baseline)
        if injected is not None:
            raise injected

        import httpx  # lazy: ~0.15 s off every CLI start (Phase F)

        client = self._client or httpx
        parts: list[str] = []
        final: dict = {}
        try:
            # INSIDE the try, because a real transport timeout is raised
            # from inside `client.stream(...)` and is converted by the
            # handler below. The first version of this injection sat ABOVE
            # the try and therefore bypassed the exact conversion it existed
            # to exercise - it produced `crashed`, which is the bug, not the
            # test of the fix. An injection outside the path it is testing
            # proves nothing about that path.
            if injecting_timeout(n):
                raise httpx.ReadTimeout(
                    "INJECTED transport timeout (ALPHA_INJECT_STALL)")
            with client.stream("POST", f"{self.endpoint}/api/chat",
                               timeout=httpx.Timeout(connect=15.0, read=900.0,
                                                     write=30.0, pool=15.0),
                               json=body) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    piece = (chunk.get("message") or {}).get("content") or ""
                    if piece:
                        parts.append(piece)
                        if on_chunk is not None:
                            # Emitted as it arrives, before the done-check, so
                            # a watcher sees the last chunk too. Never inside
                            # a try that would swallow it into the generic
                            # backend-error branch: `stream.publish` already
                            # drops a failing listener, and a display must not
                            # be able to fail a task.
                            on_chunk(piece)
                    watch.count(piece)
                    if chunk.get("done"):
                        final = chunk
                        break
                    # The rule, checked while it can still be acted on.
                    watch.judge()
        except ModelStall:
            raise
        except httpx.TimeoutException as e:
            raise watch.timeout_as_stall(self.endpoint, e, t0) from e
        except Exception as e:  # noqa: BLE001
            raise BackendError(f"ollama call failed at {self.endpoint}: {e}") from e

        text = "".join(parts)
        out_tokens = final.get("eval_count", 0) or watch.n
        watch.finish(out_tokens)
        return self._response(
            text,
            TokenUsage(
                input_tokens=final.get("prompt_eval_count", 0) or 0,
                output_tokens=out_tokens,
            ),
            t0,
            {"done_reason": final.get("done_reason")},
        )

    def available(self) -> bool:
        try:
            import httpx  # lazy: ~0.15 s off every CLI start (Phase F)

            client = self._client or httpx
            r = client.get(f"{self.endpoint}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False
