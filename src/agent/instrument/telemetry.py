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
                             wall_ms, escalated, thinking, created_at, outcome)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'returned')""",
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


def begin_call(conn: sqlite3.Connection, task_id: str, backend: str, purpose: str,
               escalated: bool = False, thinking: str = "unknown",
               model: str | None = None, est_input_tokens: int | None = None) -> int:
    """D36 - the row, written BEFORE the call. Usage is NULL until it returns."""
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
    """A call that ended without a response: its time is known, its usage is not."""
    conn.execute("UPDATE calls SET wall_ms=?, outcome=? WHERE id=?",
                 (wall_ms, outcome, call_id))
    conn.commit()


@dataclass
class TaskMetrics:
    task_id: str
    paid_tokens: int = 0
    free_tokens: int = 0
    cost_usd: float = 0.0
    # None means UNMEASURED - no backend on this task reports cache
    # accounting - and is not the same as 0.0, which means the backend
    # reported that nothing was cached. A chart that averages them
    # together is inventing data.
    cache_hit_ratio: float | None = None
    wall_ms: int = 0
    verifier_pass_rate: float = 0.0
    escalation_rate: float = 0.0
    delegation_count: int = 0
    n_refinements: int = 0
    n_calls: int = 0
    # D36: calls begun that never delivered a response - stalled, crashed,
    # interrupted, or still open because the process died. Their tokens and
    # cost are unknown, which is not zero; their time is in wall_ms when an
    # abort recorded it.
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
    """Reads the calls table. Also the router's memory of observed latency."""

    # Cache hit ratio is a monitored invariant, not a curiosity. Below this,
    # something is mutating the stable prefix and costs just went up ~10x.
    CACHE_ALARM_THRESHOLD = 0.30

    def __init__(self, conn: sqlite3.Connection, cloud_backends: set[str],
                 cache_reporting_backends: set[str] | None = None):
        self.conn = conn
        self.cloud_backends = cloud_backends
        # Which backends report prompt-cache accounting. Declared in
        # config, never sniffed from the adapter class - see
        # BackendConfig.reports_prompt_cache. Empty means nothing on this
        # config can report it, so the ratio stays None.
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
            if ti is None:                   # D36: never returned
                m.n_unreturned += 1
            total = (ti or 0) + (to or 0) + (cr or 0) + (cw or 0)
            if backend in self.cloud_backends:
                m.paid_tokens += total
            else:
                m.free_tokens += total
            m.cost_usd += cost or 0.0
            m.wall_ms += wall or 0
            escalated += esc
            # Only calls on a backend that REPORTS caching enter the ratio.
            # Mixing in calls from one that reports nothing would drag the
            # ratio toward zero and read as a prefix mutation - an alarm
            # firing on the absence of a measurement.
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
        """The monitored invariant, which until now could not fire.

        TWO independent reasons it never did, and fixing either alone leaves
        it dead:

          1. No local backend reported cache accounting, so the ratio was a
             constant 0.0 — indistinguishable from a total prefix mutation.
             Phase 1 fixed that half: `/v1` reports it. The ratio is now None
             when unmeasured, and an alarm cannot fire on None.
          2. `paid_tokens > 0`, removed here. Under `--profile local` nothing
             costs money, so that gate was never true and the alarm was
             unreachable on every local run this project has ever done.

        Reason 2 is worth being precise about, because the gate was not
        arbitrary: the spec justified this alarm by COST — a prefix mutation
        raises spend ~10x. On this hardware the same mutation is paid in
        wall clock instead, re-processing a prompt the server had already
        cached, and wall clock is the budget that actually binds. Gating a
        correctness alarm on the axis that happens to be zero here meant the
        one machine it ran on was the one machine it could not protect.

        Widening when an alarm fires is a strengthening, and the None guard
        is what keeps it from becoming noise.
        """
        m = self.metrics(task_id)
        if (m.n_calls >= 3 and m.cache_hit_ratio is not None
                and m.cache_hit_ratio < self.CACHE_ALARM_THRESHOLD):
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
                   WHERE backend=? AND purpose=? AND output_tokens IS NOT NULL
                   ORDER BY output_tokens""",
                (backend, purpose),
            ).fetchall()
        ]
        if not rows:
            return None
        return float(rows[len(rows) // 2])

    def observed_tokens_per_sec(self, backend: str, purpose: str | None = None) -> float | None:
        """Throughput from work actually done, not from a probe.

        Prefers this purpose's own history — a model that thinks before
        answering has a very different effective rate per purpose — and falls
        back to the backend's overall rate.
        """
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

