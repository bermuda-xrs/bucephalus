"""Token counting - the local estimate, and what it is and is not used for.

The build spec listed tiktoken. That is OpenAI's tokenizer: it undercounts
Claude tokens by roughly 15-20% on prose and considerably more on code, so it
was replaced by `count_tokens()`, a fast local ESTIMATE.

What the estimate is used for: the hot loop - context budgeting and the fill
ratio (M3.4, invariant 11), ledger entry sizes, delegation contracts - and the
mock backend's reported usage, so tests exercise the telemetry path.

What it is NOT used for: recorded usage. Every real backend records the model
server's own count - Ollama's `prompt_eval_count` / `eval_count`, the OpenAI
protocol's `prompt_tokens` / `completion_tokens`, Anthropic's `usage` - so the
token figures in `calls` come from the executing model's tokenizer. Where a
server omits usage, output falls back to the streamed chunk count.

The estimate's error against the executing model's tokenizer is unmeasured
(DEFERRED D6). `calibrate()` used to measure it against Anthropic's counter
instead: it needed an API key, had no caller, and would have tuned the
estimate to the wrong tokenizer for the local models that actually run here.
Deleted 2026-09-15, with `count_tokens_exact`, its only user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Empirically ~3.6 characters per token for mixed English + code on Claude
# tokenizers. Not measured against the local models' tokenizers (D6).
_CHARS_PER_TOKEN = 3.6

_WORDISH = re.compile(r"\w+|[^\w\s]")


def count_tokens(text: str) -> int:
    """Fast local estimate. Deliberately slightly pessimistic (rounds up).

    Blends a character-rate estimate with a token-ish word count so that dense
    code (many short symbols) does not get wildly under-counted.
    """
    if not text:
        return 0
    by_chars = len(text) / _CHARS_PER_TOKEN
    by_words = len(_WORDISH.findall(text)) * 0.85
    return int(max(by_chars, by_words)) + 1


MIN_CALIBRATION_CALLS = 30


@dataclass(frozen=True)
class Calibration:
    slope: float = 1.0
    intercept: float = 0.0
    n: int = 0
    fitted: bool = False

    def apply(self, prefix_tokens: int, tail_tokens: int) -> tuple[int, int]:
        if not self.fitted:
            return prefix_tokens, tail_tokens
        total = round(self.slope * (prefix_tokens + tail_tokens) + self.intercept)
        tail = round(self.slope * tail_tokens)
        return total - tail, tail


def fit(pairs) -> Calibration:
    # Ollama counts only what it evaluated, so a cache hit reports far below the estimate.
    pts = [(e, s) for e, s in pairs if e and s and s >= 0.8 * e]
    n = len(pts)
    if n < MIN_CALIBRATION_CALLS:
        return Calibration(n=n)
    mx = sum(e for e, _ in pts) / n
    my = sum(s for _, s in pts) / n
    sxx = sum((e - mx) ** 2 for e, _ in pts)
    if not sxx:
        return Calibration(n=n)
    slope = sum((e - mx) * (s - my) for e, s in pts) / sxx
    if not 0.5 <= slope <= 3.0:
        return Calibration(n=n)
    return Calibration(slope=slope, intercept=my - slope * mx, n=n, fitted=True)


def calibration(conn, backend: str, model: str) -> Calibration:
    return fit(conn.execute(
        """SELECT est_input_tokens, input_tokens FROM calls
            WHERE backend=? AND model=? AND purpose='step' AND outcome='returned'
              AND COALESCE(cache_read_tokens, 0) = 0 AND est_input_tokens IS NOT NULL
            ORDER BY id DESC LIMIT 500""", (backend, model)).fetchall())
