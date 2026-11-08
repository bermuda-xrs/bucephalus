from __future__ import annotations

import time

from ..security.origin import BENCH

# bench rows from before tasks carried origin='bench' are marked only by the [arm:X] goal suffix
_MINE = "t.origin != ? AND t.contract_json NOT LIKE '%[arm:%'"
_SUMS = ("COALESCE(SUM(c.input_tokens),0), COALESCE(SUM(c.output_tokens),0), COALESCE(SUM(c.cache_read_tokens),0), "
         "COALESCE(SUM(c.cost_usd),0), COUNT(*)")


def usage(conn, days: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    day0 = (int(now) // 86400 - days + 1) * 86400
    rows = f"FROM calls c JOIN tasks t ON t.id = c.task_id WHERE {_MINE} AND c.created_at >= ?"
    args = (BENCH, day0)
    i, o, cr, usd, n = conn.execute(f"SELECT {_SUMS} {rows}", args).fetchone()
    by_day = {int(d): r for d, *r in conn.execute(
        f"SELECT CAST(c.created_at / 86400 AS INTEGER), {_SUMS} {rows} GROUP BY 1", args)}
    tasks = dict(conn.execute(
        f"SELECT t.status, COUNT(*) FROM tasks t WHERE {_MINE} AND t.created_at >= ? GROUP BY 1", args).fetchall())
    return {
        "days": days,
        "totals": {"input_tokens": i, "output_tokens": o, "cache_read_tokens": cr, "cost_usd": usd, "calls": n,
                   "tasks": sum(tasks.values()),
                   "failed_calls": conn.execute(f"SELECT COUNT(*) {rows} AND c.outcome != 'returned'",
                                                args).fetchone()[0]},
        "daily": [dict(zip(("day", "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd", "calls"),
                           (d * 86400, *by_day.get(d, (0, 0, 0, 0, 0)))))
                  for d in range(day0 // 86400, day0 // 86400 + days)],
        "models": [{"model": m, "input_tokens": a, "output_tokens": b, "cost_usd": u, "calls": k, "last_used": last}
                   for m, a, b, _, u, k, last in conn.execute(
                       f"SELECT COALESCE(c.model, c.backend), {_SUMS}, MAX(c.created_at) {rows} "
                       "GROUP BY 1 ORDER BY SUM(c.input_tokens) + SUM(c.output_tokens) DESC", args)],
        "purposes": [{"purpose": p, "calls": k, "tokens": a + b} for p, a, b, _, _, k in conn.execute(
            f"SELECT c.purpose, {_SUMS} {rows} GROUP BY 1 ORDER BY 6 DESC", args)],
        "statuses": tasks,
    }
