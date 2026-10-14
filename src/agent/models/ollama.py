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
    reset_rates,  # noqa: F401
)


RETRY_AFTER_S = 1.0


def _dropped():
    import httpx

    return (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)


DROPPED = _dropped()


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
            "num_ctx": self.cfg.budgeted_window,
        }
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prefix},
                {"role": "user", "content": tail},
            ],
            "options": options,
            "keep_alive": self.cfg.keep_alive,
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

        import httpx

        client = self._client or httpx
        parts: list[str] = []
        calls: list[dict] = []
        final: dict = {}
        try:
            if injecting_timeout(n):
                raise httpx.ReadTimeout(
                    "INJECTED transport timeout (BUCEPHALUS_INJECT_STALL)")
            for attempt in (0, 1):
                try:
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
                            calls += (chunk.get("message") or {}).get("tool_calls") or []
                            piece = (chunk.get("message") or {}).get("content") or ""
                            if piece:
                                parts.append(piece)
                                if on_chunk is not None:
                                    on_chunk(piece)
                            watch.count(piece)
                            if chunk.get("done"):
                                final = chunk
                                break
                            watch.judge()
                    break
                except DROPPED:
                    if attempt or parts:
                        raise
                    time.sleep(RETRY_AFTER_S)
        except ModelStall:
            raise
        except httpx.TimeoutException as e:
            raise watch.timeout_as_stall(self.endpoint, e, t0) from e
        except Exception as e:  # noqa: BLE001
            raise BackendError(f"ollama call failed at {self.endpoint}: {e}") from e

        text = "".join(parts)
        if calls and isinstance(calls[0], dict) and not text.strip():
            fn = calls[0].get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            text = json.dumps({"kind": "action", "tool": fn.get("name", ""), "args": args})
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
            import httpx

            client = self._client or httpx
            r = client.get(f"{self.endpoint}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False
