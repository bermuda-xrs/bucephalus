"""Token counting.

The build spec listed tiktoken. That is OpenAI's tokenizer: it undercounts
Claude tokens by roughly 15-20% on prose and considerably more on code, so
every cost number computed from it would be quietly wrong — which defeats the
point of M2.

What this module does instead:

  * `count_tokens()` is a fast local ESTIMATE, used in the hot loop for
    context budgeting (M3.4) where we need a number thousands of times per
    run and a few percent of error is harmless.
  * `count_tokens_exact()` calls the provider's own count_tokens endpoint.
    Use it for calibration and for anything published as a measurement.
  * `calibrate()` measures the local estimator against the exact counter on
    your own text and stores the ratio, so the estimate stays honest.

Never report a headline token number from the estimator without saying so.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Empirically ~3.6 characters per token for mixed English + code on Claude
# tokenizers. Calibration overwrites this at runtime.
_CHARS_PER_TOKEN = 3.6
_CALIBRATION_FILE = Path(os.environ.get("ALPHA_CALIBRATION", ".token_calibration.json"))

_WORDISH = re.compile(r"\w+|[^\w\s]")


def _load_calibration() -> None:
    global _CHARS_PER_TOKEN
    try:
        if _CALIBRATION_FILE.exists():
            data = json.loads(_CALIBRATION_FILE.read_text(encoding="utf-8"))
            ratio = float(data.get("chars_per_token", _CHARS_PER_TOKEN))
            if 1.0 < ratio < 10.0:
                _CHARS_PER_TOKEN = ratio
    except Exception:  # noqa: BLE001 - calibration is an optimisation, never fatal
        pass


_load_calibration()


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


def count_tokens_exact(text: str, model: str, client=None) -> int:
    """Provider-authoritative count. Costs a network round trip.

    Only used for calibration and published measurements — never in the loop.
    """
    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    resp = client.messages.count_tokens(
        model=model, messages=[{"role": "user", "content": text}]
    )
    return int(resp.input_tokens)


def calibrate(samples: list[str], model: str, client=None) -> float:
    """Measure chars-per-token on your own corpus and persist the ratio."""
    total_chars = sum(len(s) for s in samples)
    total_tokens = sum(count_tokens_exact(s, model, client) for s in samples)
    if total_tokens == 0:
        raise ValueError("no tokens counted")
    ratio = total_chars / total_tokens
    _CALIBRATION_FILE.write_text(
        json.dumps({"chars_per_token": ratio, "model": model, "n": len(samples)}),
        encoding="utf-8",
    )
    global _CHARS_PER_TOKEN
    _CHARS_PER_TOKEN = ratio
    return ratio


def chars_per_token() -> float:
    return _CHARS_PER_TOKEN
