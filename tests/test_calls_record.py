from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import pytest

from agent.execute.loop import run_task
from agent.models.stall import ModelStall

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"
FINAL = '<final>{"answer": "done"}</final>'


def _rows(ctx, task_id: str) -> list[tuple]:
    return ctx.conn.execute(
        "SELECT purpose, input_tokens, output_tokens, wall_ms, outcome FROM calls "
        "WHERE task_id=? ORDER BY id", (task_id,)).fetchall()


def test_a_call_is_on_the_record_before_it_returns(ctx, task):
    seen: list = []

    def responder(prefix, tail):
        seen.append(ctx.conn.execute(
            "SELECT input_tokens, outcome FROM calls WHERE task_id=? "
            "ORDER BY id DESC LIMIT 1", (task.id,)).fetchone())
        return FINAL

    ctx.backends["cloud"]._responder = responder  # noqa: SLF001
    run_task(ctx, task.id)
    assert seen and seen[0] == (None, None), f"no open row while the call ran: {seen}"
    ((purpose, ti, to, wall, outcome),) = _rows(ctx, task.id)
    assert (purpose, outcome) == ("step", "returned") and ti and to and wall is not None


def test_a_stalled_call_keeps_its_row_and_its_time(ctx, task):
    def stall(prefix, tail):
        time.sleep(0.25)
        raise ModelStall("stalled in a test", observed=0.1, median=2.0,
                         tokens=1, elapsed=0.25)

    ctx.backends["cloud"]._responder = stall  # noqa: SLF001
    with pytest.raises(ModelStall):
        run_task(ctx, task.id)
    rows = _rows(ctx, task.id)
    assert rows, "a stalled call left no row"
    purpose, ti, to, wall, outcome = rows[0]
    assert outcome == "stalled" and ti is None and to is None, rows[0]
    assert wall is not None and wall >= 200, "the stall's time is not on the record"


def test_a_call_that_never_returns_stays_open_and_is_counted(ctx, task):
    from agent.instrument.telemetry import begin_call

    begin_call(ctx.conn, task.id, "cloud", "step")
    m = ctx.telemetry.metrics(task.id)
    assert (m.n_calls, m.n_unreturned, m.wall_ms) == (1, 1, 0)
    ((_, ti, _, wall, outcome),) = _rows(ctx, task.id)
    assert ti is None and wall is None and outcome is None


def test_the_deputy_is_on_the_record(ctx, workspace):
    from agent.plan.planner import approve_plan
    from test_m13_autonomy import _planned_task

    task, plan = _planned_task(ctx, workspace)
    approve_plan(ctx, task, plan)
    ctx.backends["cloud"].queue(
        '<action tool="write_file">'
        + json.dumps({"path": "work/out.txt", "content": "hello"}) + "</action>", FINAL)
    ctx.backends["review"].queue("VERDICT: approve\nBECAUSE: write inside artifact_scope")
    run_task(ctx, task.id)
    purposes = [r[0] for r in _rows(ctx, task.id)]
    assert "review" in purposes, purposes


def test_router_inputs_ignore_calls_that_never_returned(ctx, task):
    from agent.instrument.telemetry import abort_call, begin_call, complete_call
    from agent.kernel.types import TokenUsage

    done = begin_call(ctx.conn, task.id, "local", "summarize")
    complete_call(ctx.conn, done, TokenUsage(input_tokens=10, output_tokens=100), 0.0, 1000)
    abort_call(ctx.conn, begin_call(ctx.conn, task.id, "local", "summarize"), 9000, "stalled")
    begin_call(ctx.conn, task.id, "local", "summarize")
    begin_call(ctx.conn, task.id, "local", "summarize")
    assert ctx.telemetry.median_output_tokens("local", "summarize") == 100.0
    assert ctx.telemetry.observed_tokens_per_sec("local", "summarize") == 100.0


PROBES = {
    "context.py": "AgentContext.call_model - the one place a task's call is made",
    "models/base.py": "measure_throughput - a probe of the backend, no task",
    "cli.py": "bench-local - a throughput probe, no task",
}


def test_no_model_call_bypasses_the_record():
    offenders = []
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        if rel in PROBES:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.complete\(", line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{i}: {line.strip()[:90]}")
    assert not offenders, "model calls outside call_model:\n" + "\n".join(offenders)


OLD_CALLS = """
CREATE TABLE calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, backend TEXT NOT NULL,
    purpose TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL,
    wall_ms INTEGER NOT NULL, escalated INTEGER NOT NULL DEFAULT 0,
    thinking TEXT NOT NULL DEFAULT 'unknown', created_at REAL NOT NULL);
CREATE INDEX idx_calls_task ON calls(task_id);
"""
COLS = ("id, task_id, backend, purpose, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, cost_usd, wall_ms, escalated, thinking, created_at")


def test_the_calls_table_migrates_without_losing_a_row(tmp_path):
    from agent.kernel.db import open_db

    db = tmp_path / "pre_d36.db"
    c = sqlite3.connect(db)
    c.executescript(OLD_CALLS)
    c.executemany(
        "INSERT INTO calls(task_id, backend, purpose, input_tokens, output_tokens, "
        "cost_usd, wall_ms, created_at) VALUES (?,?,?,?,?,?,?,?)",
        [("t1", "cloud", "step", 10, 5, 0.0, 1234, 1.0),
         ("t2", "review", "review", 7, 3, 0.25, 999, 2.0)])
    c.commit()
    before = c.execute(f"SELECT {COLS} FROM calls ORDER BY id").fetchall()
    c.close()

    conn = open_db(db)
    notnull = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(calls)")}
    assert notnull["input_tokens"] == 0 and notnull["wall_ms"] == 0 and "outcome" in notnull
    assert conn.execute(f"SELECT {COLS} FROM calls ORDER BY id").fetchall() == before
    assert {r[0] for r in conn.execute("SELECT outcome FROM calls")} == {"returned"}
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' "
                        "AND name='idx_calls_task'").fetchone()
    conn.close()
    conn = open_db(db)
    assert conn.execute(f"SELECT {COLS} FROM calls ORDER BY id").fetchall() == before
    conn.close()
