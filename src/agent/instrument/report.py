"""M2.4 — per-run JSON, a CSV of all calls, and the cost-vs-quality points.

The published artifact is a Pareto frontier, not a single score (M13.2).
"""

from __future__ import annotations

import csv
import json
import platform
import sqlite3
import time
from pathlib import Path

from .. import __version__
from .telemetry import Telemetry


def run_report(conn: sqlite3.Connection, telemetry: Telemetry, task_id: str) -> dict:
    m = telemetry.metrics(task_id).as_dict()
    row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    m["status"] = row[0] if row else "unknown"
    m["cache_alarm"] = telemetry.cache_alarm(task_id)
    m["provenance"] = provenance(conn, task_id)
    return m


def provenance(conn: sqlite3.Connection, task_id: str) -> dict:
    """Every number gets reported with date, model version, scaffold version."""
    backends = sorted(
        {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT backend FROM calls WHERE task_id=?", (task_id,)
            ).fetchall()
        }
    )
    return {
        "reported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scaffold_version": __version__,
        "backends_used": backends,
        "python": platform.python_version(),
        "platform": platform.system(),
        "token_counts": "estimated locally; see src/agent/tokens.py for the caveat",
    }


def write_run_json(path: str | Path, report: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return p


def write_calls_csv(conn: sqlite3.Connection, path: str | Path, task_id: str | None = None) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "id",
        "task_id",
        "backend",
        "purpose",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_usd",
        "wall_ms",
        "escalated",
        "thinking",
        "created_at",
    ]
    q = f"SELECT {','.join(cols)} FROM calls"  # noqa: S608 - cols is a literal list
    args: tuple = ()
    if task_id:
        q += " WHERE task_id=?"
        args = (task_id,)
    rows = conn.execute(q, args).fetchall()
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return p


def pareto_points(conn: sqlite3.Connection, telemetry: Telemetry) -> list[dict]:
    """x = mean USD/task, y = pass rate — grouped by arm label in the task goal.

    Arms are whatever you tagged: baseline, skills-off, skills-on, per effort.
    """
    groups: dict[str, list[tuple[float, int]]] = {}
    for task_id, status in conn.execute("SELECT id, status FROM tasks").fetchall():
        m = telemetry.metrics(task_id)
        arm = _arm_of(conn, task_id)
        groups.setdefault(arm, []).append((m.cost_usd, 1 if status == "done" else 0))
    out = []
    for arm, pts in sorted(groups.items()):
        n = len(pts)
        out.append(
            {
                "arm": arm,
                "n": n,
                "mean_usd_per_task": round(sum(p[0] for p in pts) / n, 6),
                "pass_rate": round(sum(p[1] for p in pts) / n, 4),
            }
        )
    return out


def _arm_of(conn: sqlite3.Connection, task_id: str) -> str:
    row = conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='task.created'",
        (task_id,),
    ).fetchone()
    if not row:
        return "unlabelled"
    payload = json.loads(row[0])
    goal = payload.get("contract", {}).get("goal", "")
    if "[arm:" in goal:
        return goal.split("[arm:", 1)[1].split("]", 1)[0].strip()
    return "unlabelled"
