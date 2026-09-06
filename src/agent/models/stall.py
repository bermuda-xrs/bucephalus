"""Machine-fault detection, shared by every streaming adapter.

Lifted out of `models/ollama.py` unchanged when a second streaming adapter
arrived (iteration two, phase 1). A stall is a property of the machine, not of
an adapter: the same rule, the same rate history and the same injection switch
have to govern every backend, or `machine_fault` quietly means one thing on
Ollama's native API and something else on its OpenAI-compatible one — and the
two are the same server, generating with the same weights.

Everything below is the original code with the arithmetic moved into
`StallWatch` so the two stream loops cannot drift apart. The thresholds, the
history length and the injection grammar are untouched.

---------------------------------------------------------------------------
Stall detection.

Three times in one session a single model call ran an order of magnitude slow:
2465 s in the v3 ladder probe, 2539 s for 512 tokens in the L6 verification
run (about 0.2 tok/s against a normal 2-3), and 1499.8 s in the documented
orphan incident. They are 29x, 17x and 18x their run medians.

Why this had to be fixed rather than logged: the task budget is WALL CLOCK and
`budget_exhausted` is scored as a FAILURE. So a machine stall converts a task
that would have passed into one that failed. In a paired trial that
manufactures a discordant pair out of a machine event, and interleaving the
arms cannot help - a single stalled call lands on exactly one arm by
definition. A machine stall is not a task outcome.

Note the timeout that was already there did NOT bound this. The call carried
`timeout=600.0` and ran for 2539 s, because with `stream: False` the httpx
read timeout measures the gap between bytes, and the server sends nothing at
all until it is done. Streaming is therefore not incidental to the fix: it is
what makes a wall-clock rule enforceable, and closing the stream actually
stops the server generating, where abandoning a blocking request would leave
the worker busy and the retry queued behind it.
"""

from __future__ import annotations

import os
import statistics
import time

from .base import BackendError

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


class StallWatch:
    """The rate rule, checked while it can still be acted on.

    One instance per call, and TWO methods rather than one, because the
    original loop counted and judged at different points and collapsing them
    changes behaviour in both directions:

      * `count()` runs before the done-check, so the final chunk's tokens are
        counted. Folding it after would under-report by one chunk on any
        server that omits a usage block, which is the fallback path.
      * `judge()` runs after the done-check, so a call that has just FINISHED
        is never declared stalled. A slow call that completed produced work;
        converting it to a machine fault would discard it.

    `judge()` is called for every non-final chunk including ones carrying no
    text, which is what catches a generation that has slowed to nothing while
    the connection is still alive - the shape all three observed stalls had.
    """

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
        # Only after enough tokens and enough seconds that the estimate means
        # something - judging a rate off two tokens would abort healthy calls.
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
        """Feed the observed rate back into the history for the next call."""
        if self.first_token_at is not None:
            gen_s = max(time.time() - self.first_token_at, 1e-6)
            record_rate(self.key, out_tokens / gen_s)

    def timeout_as_stall(self, endpoint: str, exc: Exception,
                         t0: float) -> ModelStall:
        """A transport timeout IS a stall - the same machine event, caught by
        the transport instead of by the rate test.

        It reaches here when the call produced too few tokens for a rate to be
        judged at all, so the tok/s check never got the chance to fire.
        Returning the same type means both paths are handled identically by
        the caller rather than one of them landing in the generic
        backend-error branch and being scored as a task failure.
        """
        return ModelStall(
            f"model call timed out at {endpoint} after producing {self.n} "
            f"tokens - aborted as a machine fault, not a task outcome: {exc}",
            0.0, self.baseline or 0.0, self.n,
            time.time() - (self.first_token_at or t0))


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
#
# The counter is deliberately shared across adapters rather than per-adapter.
# A run uses one executor, and an index that meant "the third Ollama call" on
# one backend and "the third OpenAI call" on another would make an injected
# shakedown unreproducible across the very config switch phase 1 exists to
# make safe.
_CALLS = {"n": 0}


def next_call_index() -> int:
    """Advance and return the process-wide model-call counter."""
    _CALLS["n"] += 1
    return _CALLS["n"]


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


def injected_stall(n: int, baseline: float | None) -> ModelStall | None:
    """The stall to raise on call `n`, or None.

    One read of the env, one place, fails closed on garbage - the property
    `test_injection_cannot_fire_without_the_flag` pins.
    """
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
