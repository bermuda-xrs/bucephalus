from __future__ import annotations

import re
from dataclasses import dataclass

_CHARS_PER_TOKEN = 3.6

_WORDISH = re.compile(r"\w+|[^\w\s]")


def count_tokens(text: str) -> int:
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
