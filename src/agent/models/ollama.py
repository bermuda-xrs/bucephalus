"""M4.3 / M5.4 — the Ollama adapter.

Ollama keeps the prompt in its own KV cache; a stable prefix still helps
latency even though it costs nothing.

Constrained output matters most here, where format adherence is weakest. Pass
a JSON Schema through `format` rather than parsing-and-retrying — every
retry-for-format is pure wasted tokens.
"""

from __future__ import annotations

import json
import os
import statistics
import time

import httpx

from ..config import BackendConfig
from ..kernel.types import ModelResponse, TokenUsage
from .base import BackendBase, BackendError

# ---------------------------------------------------------------------------
# Stall detection.
#
# Three times in one session a single model call ran an order of magnitude
# slow: 2465 s in the v3 ladder probe, 2539 s for 512 tokens in the L6
# verification run (about 0.2 tok/s against a normal 2-3), and 1499.8 s in the
# documented orphan incident. They are 29x, 17x and 18x their run medians.
#
# Why this had to be fixed rather than logged: the task budget is WALL CLOCK
# and `budget_exhausted` is scored as a FAILURE. So a machine stall converts a
# task that would have passed into one that failed. In a paired trial that
# manufactures a discordant pair out of a machine event, and interleaving the
# arms cannot help - a single stalled call lands on exactly one arm by
# definition. A machine stall is not a task outcome.
#
# Note the timeout that was already there did NOT bound this. The call carried
# `timeout=600.0` and ran for 2539 s, because with `stream: False` httpx's
# read timeout measures the gap between bytes, and the server sends nothing at
# all until it is done. Streaming is therefore not incidental to the fix: it
# is what makes a wall-clock rule enforceable, and closing the stream actually
# stops the server generating, where abandoning a blocking request would leave
# the worker busy and the retry queued behind it.
_RATES: dict[str, list[float]] = {}

STALL_FRACTION = 0.25          # abort below a quarter of the running median
MIN_RATE_SAMPLES = 3           # no median, no judgement
MIN_TOKENS_BEFORE_JUDGING = 24
MIN_SECONDS_BEFORE_JUDGING = 45.0
RATE_HISTORY = 50


class ModelStall(BackendError):
    """A call generating far below this executor's established rate.

    Subclasses BackendError so nothing that already handles backend trouble
    breaks, but callers that want to record it as `machine_fault` rather than
    as a task failure can catch it specifically.
    """

    def __init__(self, msg: str, observed: float, median: float,
                 tokens: int, elapsed: float):
        super().__init__(msg)
        self.observed_rate = observed
        self.median_rate = median
        self.tokens = tokens
        self.elapsed = elapsed


def record_rate(key: str, tokens_per_s: float) -> None:
    if tokens_per_s > 0:
        h = _RATES.setdefault(key, [])
        h.append(tokens_per_s)
        del h[:-RATE_HISTORY]


def median_rate(key: str) -> float | None:
    h = _RATES.get(key) or []
    return statistics.median(h) if len(h) >= MIN_RATE_SAMPLES else None


def reset_rates() -> None:
    """Rates are per executor; a different model is a different baseline."""
    _RATES.clear()


# ---------------------------------------------------------------------------
# Fault injection, for proving the machine-fault path works.
#
# Stalls have occurred about three times in sixty tasks. A short shakedown run
# will almost certainly see none, and "no stall occurred" is not evidence the
# handler works - it is the same absence of evidence that let the wiring
# defect survive two aborted attempts. So the condition is INJECTED rather
# than waited for.
#
# ALPHA_INJECT_STALL is a comma-separated list of 1-based model-call indices,
# counted from process start, on which `complete()` raises ModelStall instead
# of calling the server. Unset - which is every normal run - and nothing here
# can fire: `_inject_on()` returns an empty set and the counter is never even
# compared. `test_injection_cannot_fire_without_the_flag` holds that line.
_CALLS = {"n": 0}


def _inject_on() -> set[int]:
    """Call indices that raise ModelStall directly."""
    return {n for n, kind in _inject_spec().items() if kind == "stall"}


def _inject_timeout_on() -> set[int]:
    """Call indices that raise a TRANSPORT timeout instead.

    A separate path on purpose. H30 stalled with too few tokens for a rate to
    be judged, so it never hit the tok/s test and surfaced as a plain
    BackendError - which the generic handler scored as a task failure.
    Injecting a real httpx timeout exercises the conversion, where injecting a
    ModelStall would skip the very step that was broken.
    """
    return {n for n, kind in _inject_spec().items() if kind == "timeout"}


def _inject_spec() -> dict[int, str]:
    raw = os.environ.get("ALPHA_INJECT_STALL", "").strip()
    if not raw:
        return {}
    out: dict[int, str] = {}
    for part in raw.split(","):
        part = part.strip().lower()
        kind = "stall"
        if part.endswith("t"):
            kind, part = "timeout", part[:-1]
        if part.isdigit() and int(part) > 0:
            out[int(part)] = kind
    return out


def reset_injection_counter() -> None:
    _CALLS["n"] = 0


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
        rate_key = f"{self.endpoint}|{self.model}"
        baseline = median_rate(rate_key)

        _CALLS["n"] += 1
        if _CALLS["n"] in _inject_on():
            raise ModelStall(
                f"INJECTED stall on model call {_CALLS['n']} "
                "(ALPHA_INJECT_STALL) - aborted as a machine fault, not a "
                "task outcome",
                0.01, baseline or 2.5, 0, 0.0)

        client = self._client or httpx
        parts: list[str] = []
        final: dict = {}
        first_token_at: float | None = None
        n_chunks = 0
        try:
            # INSIDE the try, because a real transport timeout is raised
            # from inside `client.stream(...)` and is converted by the
            # handler below. The first version of this injection sat ABOVE
            # the try and therefore bypassed the exact conversion it existed
            # to exercise - it produced `crashed`, which is the bug, not the
            # test of the fix. An injection outside the path it is testing
            # proves nothing about that path.
            if _CALLS["n"] in _inject_timeout_on():
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
                        n_chunks += 1
                        if first_token_at is None:
                            first_token_at = time.time()
                    if chunk.get("done"):
                        final = chunk
                        break

                    # The rule, checked while it can still be acted on. Only
                    # after enough tokens and enough seconds that the estimate
                    # means something - judging a rate off two tokens would
                    # abort healthy calls.
                    if (baseline and first_token_at
                            and n_chunks >= MIN_TOKENS_BEFORE_JUDGING):
                        elapsed = time.time() - first_token_at
                        if elapsed >= MIN_SECONDS_BEFORE_JUDGING:
                            observed = n_chunks / elapsed
                            if observed < STALL_FRACTION * baseline:
                                raise ModelStall(
                                    f"model call stalled at {observed:.2f} tok/s "
                                    f"against a median of {baseline:.2f} "
                                    f"({n_chunks} tokens in {elapsed:.0f}s) - "
                                    "aborted as a machine fault, not a task "
                                    "outcome",
                                    observed, baseline, n_chunks, elapsed)
        except ModelStall:
            raise
        except httpx.TimeoutException as e:
            # A timeout IS a stall - the same machine event, caught by the
            # transport instead of by the rate test. It reaches here when the
            # call produced too few tokens for a rate to be judged at all, so
            # the tok/s check never got the chance to fire. Raising the same
            # type means both paths are handled identically by the caller
            # rather than one of them landing in the generic backend-error
            # branch and being scored as a task failure.
            raise ModelStall(
                f"model call timed out at {self.endpoint} after producing "
                f"{n_chunks} tokens - aborted as a machine fault, not a task "
                f"outcome: {e}",
                0.0, baseline or 0.0, n_chunks,
                time.time() - (first_token_at or t0)) from e
        except Exception as e:  # noqa: BLE001
            raise BackendError(f"ollama call failed at {self.endpoint}: {e}") from e

        text = "".join(parts)
        out_tokens = final.get("eval_count", 0) or n_chunks
        if first_token_at is not None:
            gen_s = max(time.time() - first_token_at, 1e-6)
            record_rate(rate_key, out_tokens / gen_s)
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
            client = self._client or httpx
            r = client.get(f"{self.endpoint}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False
