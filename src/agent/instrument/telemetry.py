from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from ..kernel.types import TokenUsage


def compute_cost(usage: TokenUsage, backend_cfg: dict) -> float:
    return (
        usage.input_tokens * backend_cfg["input_cost_per_mtok"] / 1e6
        + usage.output_tokens * backend_cfg["output_cost_per_mtok"] / 1e6
        + usage.cache_read_tokens * backend_cfg["cache_read_per_mtok"] / 1e6
        + usage.cache_write_tokens * backend_cfg["cache_write_per_mtok"] / 1e6
    )


def begin_call(conn: sqlite3.Connection, task_id: str, backend: str, purpose: str,
               escalated: bool = False, thinking: str = "unknown",
               model: str | None = None, est_input_tokens: int | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO calls(task_id, backend, model, purpose, escalated, thinking,
                             est_input_tokens, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (task_id, backend, model, purpose, int(escalated), thinking, est_input_tokens,
         time.time()),
    )
    conn.commit()
    return cur.lastrowid


def complete_call(conn: sqlite3.Connection, call_id: int, usage: TokenUsage,
                  cost: float, wall_ms: int) -> None:
    conn.execute(
        """UPDATE calls SET input_tokens=?, output_tokens=?, cache_read_tokens=?,
                            cache_write_tokens=?, cost_usd=?, wall_ms=?,
                            outcome='returned'
           WHERE id=?""",
        (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
         usage.cache_write_tokens, cost, wall_ms, call_id),
    )
    conn.commit()


def abort_call(conn: sqlite3.Connection, call_id: int, wall_ms: int, outcome: str) -> None:
    conn.execute("UPDATE calls SET wall_ms=?, outcome=? WHERE id=?",
                 (wall_ms, outcome, call_id))
    conn.commit()


@dataclass
class TaskMetrics:
    task_id: str
    paid_tokens: int = 0
    free_tokens: int = 0
    cost_usd: float = 0.0
    cache_hit_ratio: float | None = None
    wall_ms: int = 0
    verifier_pass_rate: float = 0.0
    escalation_rate: float = 0.0
    delegation_count: int = 0
    n_refinements: int = 0
    n_calls: int = 0
    n_unreturned: int = 0

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "paid_tokens": self.paid_tokens,
            "free_tokens": self.free_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "cache_hit_ratio": (None if self.cache_hit_ratio is None
                                else round(self.cache_hit_ratio, 4)),
            "wall_ms": self.wall_ms,
            "verifier_pass_rate": round(self.verifier_pass_rate, 4),
            "escalation_rate": round(self.escalation_rate, 4),
            "delegation_count": self.delegation_count,
            "n_refinements": self.n_refinements,
            "n_calls": self.n_calls,
            "n_unreturned": self.n_unreturned,
        }


class Telemetry:
    CACHE_ALARM_THRESHOLD = 0.30

    def __init__(self, conn: sqlite3.Connection, cloud_backends: set[str],
                 cache_reporting_backends: set[str] | None = None):
        self.conn = conn
        self.cloud_backends = cloud_backends
        self.cache_reporting = cache_reporting_backends or set()

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
        measurable = False
        for backend, ti, to, cr, cw, cost, wall, esc in rows:
            if ti is None:
                m.n_unreturned += 1
            total = (ti or 0) + (to or 0) + (cr or 0) + (cw or 0)
            if backend in self.cloud_backends:
                m.paid_tokens += total
            else:
                m.free_tokens += total
            m.cost_usd += cost or 0.0
            m.wall_ms += wall or 0
            escalated += esc
            if backend in self.cache_reporting and ti is not None:
                measurable = True
                cache_read += cr or 0
                raw_input += ti
        m.n_calls = len(rows)
        denom = cache_read + raw_input
        m.cache_hit_ratio = (cache_read / denom) if (measurable and denom) else None
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
        if (m.n_calls >= 3 and m.cache_hit_ratio is not None
                and m.cache_hit_ratio < self.CACHE_ALARM_THRESHOLD):
            return (
                f"cache_hit_ratio={m.cache_hit_ratio:.2f} below "
                f"{self.CACHE_ALARM_THRESHOLD}: something is mutating the stable prefix"
            )
        return None

    def median_output_tokens(self, backend: str, purpose: str) -> float | None:
        # the upper median, read off the index: routing asks this on every decision, the table only grows
        row = self.conn.execute(
            """SELECT output_tokens FROM calls
               WHERE backend=? AND purpose=? AND output_tokens IS NOT NULL
               ORDER BY output_tokens LIMIT 1 OFFSET
               (SELECT COUNT(*) / 2 FROM calls WHERE backend=? AND purpose=? AND output_tokens IS NOT NULL)""",
            (backend, purpose, backend, purpose),
        ).fetchone()
        return float(row[0]) if row else None

    def observed_tokens_per_sec(self, backend: str, purpose: str | None = None) -> float | None:
        for where, args in (
            ("AND purpose=?", (backend, purpose)) if purpose else ("", (backend,)),
            ("", (backend,)),
        ):
            row = self.conn.execute(
                f"SELECT COALESCE(SUM(output_tokens),0), COALESCE(SUM(wall_ms),0) "  # noqa: S608
                f"FROM calls WHERE backend=? AND output_tokens IS NOT NULL {where}",
                args,
            ).fetchone()
            tokens, wall_ms = row[0] or 0, row[1] or 0
            if tokens > 0 and wall_ms > 0:
                return tokens / (wall_ms / 1000.0)
        return None
