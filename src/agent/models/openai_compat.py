from __future__ import annotations

import json
import os
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
)


def to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    out = []
    for t in tools:
        if t.get("type") == "function":
            out.append(t)
            continue
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or t.get("parameters")
                or {"type": "object", "properties": {}},
            },
        })
    return out


class OpenAICompatibleBackend(BackendBase):
    def __init__(self, name: str, cfg: BackendConfig,
                 client: httpx.Client | None = None):
        super().__init__(name, cfg)
        self.model = cfg.model
        self.base_url = cfg.resolved_base_url
        self._client = client

    @property
    def endpoint(self) -> str:
        return self.base_url

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        env = self.cfg.api_key_env
        if env:
            key = os.environ.get(env)
            if not key:
                raise BackendError(
                    f"backend {self.name!r} declares api_key_env={env!r} but "
                    f"{env} is unset — refusing to call an authenticated "
                    "endpoint anonymously"
                )
            h["Authorization"] = f"Bearer {key}"
        return h

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
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prefix},
                {"role": "user", "content": tail},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            self.cfg.max_tokens_field: min(max_tokens, self.cfg.max_output_tokens),
        }
        if self.cfg.accepts_temperature:
            body["temperature"] = temperature
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema,
                                "strict": False},
            }
        converted = to_openai_tools(tools)
        if converted:
            body["tools"] = converted

        watch = StallWatch(f"{self.base_url}|{self.model}")

        n = next_call_index()
        injected = injected_stall(n, watch.baseline)
        if injected is not None:
            raise injected

        import httpx

        client = self._client or httpx
        parts: list[str] = []
        calls: dict[int, dict] = {}
        usage_block: dict = {}
        finish_reason: str | None = None
        try:
            if injecting_timeout(n):
                raise httpx.ReadTimeout(
                    "INJECTED transport timeout (BUCEPHALUS_INJECT_STALL)")
            with client.stream("POST", f"{self.base_url}/chat/completions",
                               timeout=httpx.Timeout(connect=15.0, read=900.0,
                                                     write=30.0, pool=15.0),
                               headers=self._headers(),
                               json=body) as r:
                _raise_for_status(r, self.base_url)
                done = False
                for line in r.iter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        done = True
                        break
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        continue
                    if chunk.get("usage"):
                        usage_block = chunk["usage"]
                    choices = chunk.get("choices") or []
                    piece = ""
                    if choices:
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content") or ""
                        for tc in delta.get("tool_calls") or []:
                            fn = tc.get("function") or {}
                            c = calls.setdefault(tc.get("index", 0), {"name": "", "arguments": ""})
                            c["name"] = fn.get("name") or c["name"]
                            c["arguments"] += fn.get("arguments") or ""
                        finish_reason = choices[0].get("finish_reason") or finish_reason
                    if piece:
                        parts.append(piece)
                        if on_chunk is not None:
                            on_chunk(piece)
                    watch.count(piece)
                    if finish_reason:
                        continue
                    watch.judge()
                if not done and not finish_reason and not parts and not calls:
                    raise BackendError(
                        f"stream from {self.base_url} ended with no content "
                        "and no finish_reason"
                    )
        except ModelStall:
            raise
        except httpx.TimeoutException as e:
            raise watch.timeout_as_stall(self.base_url, e, t0) from e
        except BackendError:
            raise
        except Exception as e:  # noqa: BLE001
            raise BackendError(
                f"openai-compatible call failed at {self.base_url}: {e}") from e

        text = "".join(parts)
        if calls:
            first = calls[min(calls)]
            try:
                args = json.loads(first["arguments"] or "{}")
            except ValueError:
                args = {}
            text = json.dumps({"kind": "action", "tool": first["name"], "args": args if isinstance(args, dict) else {}})
        out_tokens = int(usage_block.get("completion_tokens") or 0) or watch.n
        watch.finish(out_tokens)
        return self._response(
            text,
            TokenUsage(
                input_tokens=int(usage_block.get("prompt_tokens") or 0),
                output_tokens=out_tokens,
                cache_read_tokens=int(
                    (usage_block.get("prompt_tokens_details") or {})
                    .get("cached_tokens") or 0),
            ),
            t0,
            {"finish_reason": finish_reason,
             "usage_reported": bool(usage_block)},
        )

    def available(self) -> bool:
        try:
            import httpx

            client = self._client or httpx
            r = client.get(f"{self.base_url}/models",
                           headers=self._headers(), timeout=2.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False


def _raise_for_status(r, base_url: str) -> None:
    if r.status_code < 400:
        return
    try:
        r.read()
        detail = r.text[:800]
    except Exception:  # noqa: BLE001
        detail = "<body unreadable>"
    raise BackendError(f"{base_url} returned HTTP {r.status_code}: {detail}")
