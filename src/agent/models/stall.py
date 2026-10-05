from __future__ import annotations

import os
import statistics
import time

from .base import BackendError

_RATES: dict[str, list[float]] = {}

STALL_FRACTION = 0.25
MIN_RATE_SAMPLES = 3
MIN_TOKENS_BEFORE_JUDGING = 24
MIN_SECONDS_BEFORE_JUDGING = 45.0
RATE_HISTORY = 50


class ModelStall(BackendError):
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
    _RATES.clear()


class StallWatch:
    def __init__(self, rate_key: str):
        self.key = rate_key
        self.baseline = median_rate(rate_key)
        self.first_token_at: float | None = None
        self.n = 0

    def count(self, piece: str) -> None:
        if not piece:
            return
        self.n += 1
        if self.first_token_at is None:
            self.first_token_at = time.time()

    def judge(self) -> None:
        if not (self.baseline and self.first_token_at
                and self.n >= MIN_TOKENS_BEFORE_JUDGING):
            return
        elapsed = time.time() - self.first_token_at
        if elapsed < MIN_SECONDS_BEFORE_JUDGING:
            return
        observed = self.n / elapsed
        if observed < STALL_FRACTION * self.baseline:
            raise ModelStall(
                f"model call stalled at {observed:.2f} tok/s against a median "
                f"of {self.baseline:.2f} ({self.n} tokens in {elapsed:.0f}s) - "
                "aborted as a machine fault, not a task outcome",
                observed, self.baseline, self.n, elapsed)

    def finish(self, out_tokens: int) -> None:
        if self.first_token_at is not None:
            gen_s = max(time.time() - self.first_token_at, 1e-6)
            record_rate(self.key, out_tokens / gen_s)

    def timeout_as_stall(self, endpoint: str, exc: Exception,
                         t0: float) -> ModelStall:
        return ModelStall(
            f"model call timed out at {endpoint} after producing {self.n} "
            f"tokens - aborted as a machine fault, not a task outcome: {exc}",
            0.0, self.baseline or 0.0, self.n,
            time.time() - (self.first_token_at or t0))


_CALLS = {"n": 0}


def next_call_index() -> int:
    _CALLS["n"] += 1
    return _CALLS["n"]


def _inject_on() -> set[int]:
    return {n for n, kind in _inject_spec().items() if kind == "stall"}


def _inject_timeout_on() -> set[int]:
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


def injected_stall(n: int, baseline: float | None) -> ModelStall | None:
    if n not in _inject_on():
        return None
    return ModelStall(
        f"INJECTED stall on model call {n} (ALPHA_INJECT_STALL) - aborted as "
        "a machine fault, not a task outcome",
        0.01, baseline or 2.5, 0, 0.0)


def injecting_timeout(n: int) -> bool:
    return n in _inject_timeout_on()


def reset_injection_counter() -> None:
    _CALLS["n"] = 0
