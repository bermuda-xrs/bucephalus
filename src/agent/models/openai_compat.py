"""One adapter for the OpenAI chat-completions protocol.

Targets, all speaking the same wire format: Ollama's `/v1` shim, vLLM,
llama.cpp's server, and hosted OpenAI-compatible endpoints. Switching between
them is a config edit — `base_url`, `model`, `api_key_env` — and nothing here
branches on which one is behind the URL.

Written against httpx directly rather than the `openai` package, for the same
reason `net/fetch.py` exists rather than a server-side fetch tool: the stall
detector needs the raw stream. A client library that hands back a completed
object has already spent the wall clock this project budgets in, and the one
measured failure mode that matters here — generation collapsing to a fraction
of its established rate — is invisible from the outside of a blocking call.

WHAT IS NOT HERE, deliberately:

  * No `keep_alive`. MEASURED 2026-09-06: Ollama's /v1 shim accepts the field
    and SILENTLY DISCARDS it — HTTP 200, and the model still expires on the
    server default. Unload first, call /v1 with `keep_alive: "30m"`, read
    /api/ps: 5.0 minutes, identical to sending nothing. Sending it anyway
    would put a setting in the request that reads as honoured and is not.
    On a CPU tier this is not cosmetic: a cold load measured ~30s against ~8s
    warm, and wall clock is the budget that binds. Set `OLLAMA_KEEP_ALIVE` in
    the server environment instead — see `ollama-serve.ps1`. SPEC-DELTA C28.
  * No thinking/effort. The protocol has no equivalent field; sending one gets
    it ignored by some servers and 400'd by others.
  * No silent degradation of `response_format`. If the server rejects a JSON
    schema this raises, because quietly dropping the constraint changes what
    the model was asked and therefore what the run measured.
"""

from __future__ import annotations

import json
import os
import time

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
    """Convert this project's tool schemas to the OpenAI function shape.

    `ToolRegistry.schemas()` emits `{name, description, input_schema}`, which
    is the Anthropic shape. Passing that to an OpenAI-compatible server is a
    400, so the conversion is here rather than at the call sites. Anything
    already in the OpenAI shape is passed through untouched, so a caller that
    knows what it wants is not fought with.
    """
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
        """Named `endpoint` to match the other adapters; it is what the rate
        key and every error message report."""
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
    ) -> ModelResponse:
        t0 = time.time()
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prefix},
                {"role": "user", "content": tail},
            ],
            "stream": True,
            # Ask for a usage block on the final chunk. VERIFIED against
            # Ollama 0.33.3 only — `usage_reported` comes back true there.
            # Whether vLLM, llama.cpp and hosted endpoints ignore an unknown
            # field or 400 on it is UNVERIFIED from this machine, so the
            # fallback below counts chunks rather than trusting this to
            # arrive. If a server rejects it, the error carries the server's
            # own message and this is the first line to try removing.
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

        client = self._client or httpx
        parts: list[str] = []
        usage_block: dict = {}
        finish_reason: str | None = None
        try:
            # Inside the try for the same reason as the Ollama adapter: a
            # transport timeout has to travel the conversion path below, and
            # an injection that sits above the try tests nothing about it.
            if injecting_timeout(n):
                raise httpx.ReadTimeout(
                    "INJECTED transport timeout (ALPHA_INJECT_STALL)")
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
                        continue          # blank separator or SSE keepalive
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
                        piece = (choices[0].get("delta") or {}).get("content") or ""
                        finish_reason = choices[0].get("finish_reason") or finish_reason
                    if piece:
                        parts.append(piece)
                    watch.count(piece)
                    # `finish_reason` marks the end of generation, but a
                    # usage-bearing chunk normally follows it, so the loop
                    # keeps reading until [DONE]. Judging after the model has
                    # stopped would convert a slow-but-complete call into a
                    # machine fault and throw its work away.
                    if finish_reason:
                        continue
                    watch.judge()
                if not done and not finish_reason and not parts:
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
        out_tokens = int(usage_block.get("completion_tokens") or 0) or watch.n
        watch.finish(out_tokens)
        return self._response(
            text,
            TokenUsage(
                input_tokens=int(usage_block.get("prompt_tokens") or 0),
                output_tokens=out_tokens,
                # Prompt caching, and the reason this adapter is worth more
                # than protocol tidiness.
                #
                # MEASURED 2026-09-06 against Ollama 0.33.3, same server and
                # weights as the native adapter, on a 206-token prompt the
                # server had provably never seen: /v1 reported cached_tokens
                # 5 then 205 across two identical calls, and /api/chat
                # reported nothing at all, both times. The audit recorded the
                # prefix benefit on the local tier as UNMEASURABLE for want
                # of cache accounting. It is measurable; it was being
                # discarded by the protocol, not withheld by the server.
                #
                # Absent still means unknown, and unknown is recorded as zero
                # rather than guessed — a server that does not report this is
                # not a server that cached nothing.
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
            client = self._client or httpx
            r = client.get(f"{self.base_url}/models",
                           headers=self._headers(), timeout=2.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False


def _raise_for_status(r, base_url: str) -> None:
    """Fail with the server's own message, which is where the useful part is.

    `httpx.Response.raise_for_status()` on a streamed response reports the
    status and nothing else; an OpenAI-compatible server puts the reason a
    request was rejected — an unsupported `response_format`, a model that is
    not loaded, a bad key — in the body. Losing that turns a five-second fix
    into a guessing game.
    """
    if r.status_code < 400:
        return
    try:
        r.read()
        detail = r.text[:800]
    except Exception:  # noqa: BLE001
        detail = "<body unreadable>"
    raise BackendError(f"{base_url} returned HTTP {r.status_code}: {detail}")
