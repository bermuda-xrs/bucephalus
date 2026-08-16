"""M2 — cost computation and call recording.

Every model call goes through record_call. If a call bypasses this, the numbers
are wrong and nobody finds out.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from ..kernel.types import TokenUsage


def compute_cost(usage: TokenUsage, backend_cfg: dict) -> float:
    """Four token types, four prices. Do NOT use a blended rate."""
    return (
        usage.input_tokens * backend_cfg["input_cost_per_mtok"] / 1e6
        + usage.output_tokens * backend_cfg["output_cost_per_mtok"] / 1e6
        + usage.cache_read_tokens * backend_cfg["cache_read_per_mtok"] / 1e6
        + usage.cache_write_tokens * backend_cfg["cache_write_per_mtok"] / 1e6
    )


def record_call(
    conn: sqlite3.Connection,
    task_id: str,
    backend: str,
    purpose: str,
    usage: TokenUsage,
    cost: float,
    wall_ms: int,
    escalated: bool = False,
    thinking: str = "unknown",
) -> None:
    conn.execute(
        """INSERT INTO calls(task_id, backend, purpose, input_tokens, output_tokens,
                             cache_read_tokens, cache_write_tokens, cost_usd,
                             wall_ms, escalated, thinking, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            task_id,
            backend,
            purpose,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
            cost,
            wall_ms,
            int(escalated),
            thinking,
            time.time(),
        ),
    )
    conn.commit()


@dataclass
class TaskMetrics:
    task_id: str
    paid_tokens: int = 0
    free_tokens: int = 0
    cost_usd: float = 0.0
    cache_hit_ratio: float = 0.0
    wall_ms: int = 0
    verifier_pass_rate: float = 0.0
    escalation_rate: float = 0.0
    delegation_count: int = 0
    n_refinements: int = 0
    n_calls: int = 0

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "paid_tokens": self.paid_tokens,
            "free_tokens": self.free_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "cache_hit_ratio": round(self.cache_hit_ratio, 4),
            "wall_ms": self.wall_ms,
            "verifier_pass_rate": round(self.verifier_pass_rate, 4),
            "escalation_rate": round(self.escalation_rate, 4),
            "delegation_count": self.delegation_count,
            "n_refinements": self.n_refinements,
            "n_calls": self.n_calls,
        }


class Telemetry:
    """Reads the calls table. Also the router's memory of observed latency."""

    # Cache hit ratio is a monitored invariant, not a curiosity. Below this,
    # something is mutating the stable prefix and costs just went up ~10x.
    CACHE_ALARM_THRESHOLD = 0.30

    def __init__(self, conn: sqlite3.Connection, cloud_backends: set[str]):
        self.conn = conn
        self.cloud_backends = cloud_backends

    def metrics(self, task_id: str) -> TaskMetrics:
        m = TaskMetrics(task_id=task_id)
        rows = self.conn.execute(
            """SELECT backend, input_tokens, output_tokens, cache_read_tokens,
                      cache_write_tokens, cost_usd, wall_ms, escalated
               FROM calls WHERE task_id=?""",
            (task_id,),
        ).fetchall()
        cache_read = 0
        raw_input = 0
        escalated = 0
        for backend, ti, to, cr, cw, cost, wall, esc in rows:
            total = ti + to + cr + cw
            if backend in self.cloud_backends:
                m.paid_tokens += total
            else:
                m.free_tokens += total
            m.cost_usd += cost
            m.wall_ms += wall
            cache_read += cr
            raw_input += ti
            escalated += esc
        m.n_calls = len(rows)
        denom = cache_read + raw_input
        m.cache_hit_ratio = (cache_read / denom) if denom else 0.0
        m.escalation_rate = (escalated / len(rows)) if rows else 0.0

        attempted = self._count_events(task_id, "verify.attempted")
        passed = self._count_events(task_id, "verify.passed")
        m.verifier_pass_rate = (passed / attempted) if attempted else 0.0
        m.delegation_count = self._count_events(task_id, "delegation.opened")
        m.n_refinements = max(0, attempted - 1)
        return m

    def _count_events(self, task_id: str, etype: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND type=?", (task_id, etype)
        ).fetchone()[0]

    def cache_alarm(self, task_id: str) -> str | None:
        m = self.metrics(task_id)
        if m.n_calls >= 3 and m.paid_tokens > 0 and m.cache_hit_ratio < self.CACHE_ALARM_THRESHOLD:
            return (
                f"cache_hit_ratio={m.cache_hit_ratio:.2f} below "
                f"{self.CACHE_ALARM_THRESHOLD}: something is mutating the stable prefix"
            )
        return None

    def median_output_tokens(self, backend: str, purpose: str) -> float | None:
        """Router input. Observed length PER PURPOSE, not a global tokens/sec."""
        rows = [
            r[0]
            for r in self.conn.execute(
                """SELECT output_tokens FROM calls
                   WHERE backend=? AND purpose=? ORDER BY output_tokens""",
                (backend, purpose),
            ).fetchall()
        ]
        if not rows:
            return None
        return float(rows[len(rows) // 2])

    def totals(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_usd),0), COALESCE(SUM(wall_ms),0) FROM calls"
        ).fetchone()
        return {"n_calls": row[0], "cost_usd": row[1], "wall_ms": row[2]}
