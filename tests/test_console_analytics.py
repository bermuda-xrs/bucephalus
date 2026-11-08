from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.config import Limits
from agent.control.server import create_app
from agent.instrument.telemetry import abort_call, begin_call, complete_call
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract, TokenUsage
from agent.security import origin as O
from test_console_reskin_guards import _calls

ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "src" / "agent" / "control" / "ui.js").read_text(encoding="utf-8")


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_a_bench_task_behaves_exactly_like_a_local_one():
    assert O.BENCH == "bench"
    assert O.policy_for(O.BENCH) == O.policy_for(O.LOCAL)
    assert not O.is_channel(O.BENCH) and not O.is_unattended(O.BENCH)
    b, lim = Budget(usd=5.0, seconds=900), Limits()
    assert O.clamp_budget(b, lim, O.BENCH) == O.clamp_budget(b, lim, O.LOCAL)


@pytest.mark.parametrize("path", ["bench/run.py", "src/agent/skills/gate.py", "src/agent/evals.py"])
def test_every_measurement_run_marks_its_tasks_as_bench(path):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "create_task"]
    assert calls and all(any(k.arg == "origin" and ast.unparse(k.value) == "BENCH" for k in c.keywords)
                         for c in calls)


def _task(ctx, goal, origin="local"):
    return create_task(ctx.conn, ctx.log, TaskContract(goal=goal), Budget(usd=1.0, seconds=60), origin=origin).id


def _call(ctx, tid, model, purpose, i, o, usd, at=None):
    cid = begin_call(ctx.conn, tid, "cloud", purpose, model=model)
    complete_call(ctx.conn, cid, TokenUsage(input_tokens=i, output_tokens=o, cache_read_tokens=3), usd, 100)
    if at is not None:
        ctx.conn.execute("UPDATE calls SET created_at=? WHERE id=?", (at, cid))
        ctx.conn.commit()


def test_analytics_counts_console_work_and_leaves_bench_runs_out(ctx, api):
    mine = _task(ctx, "tidy the notes")
    _call(ctx, mine, "qwen2.5:3b", "plan", 100, 20, 0.0)
    _call(ctx, mine, "qwen2.5:3b", "step", 300, 40, 0.0)
    _call(ctx, _task(ctx, "measured", origin=O.BENCH), "qwen2.5:3b", "step", 9000, 900, 1.0)
    _call(ctx, _task(ctx, "old bench task [arm:B2]"), "qwen2.5:3b", "step", 7000, 700, 1.0)
    _call(ctx, mine, "qwen2.5:3b", "step", 5000, 500, 0.0, at=time.time() - 40 * 86400)
    abort_call(ctx.conn, begin_call(ctx.conn, mine, "cloud", "step", model="qwen2.5:3b"), 30000, "stalled")
    d = api.get("/analytics?days=30").json()
    assert d["days"] == 30
    assert d["totals"] == {"input_tokens": 400, "output_tokens": 60, "cache_read_tokens": 6, "cost_usd": 0.0,
                           "calls": 3, "tasks": 1, "failed_calls": 1}
    assert sum(x["input_tokens"] for x in d["daily"]) == 400 and len(d["daily"]) == 30
    assert d["models"] == [{"model": "qwen2.5:3b", "input_tokens": 400, "output_tokens": 60, "cost_usd": 0.0,
                            "calls": 3, "last_used": d["models"][0]["last_used"]}]
    assert {p["purpose"]: p["calls"] for p in d["purposes"]} == {"plan": 1, "step": 2}
    assert d["statuses"] == {"pending": 1}


def test_analytics_on_an_empty_database_is_all_zero(api):
    d = api.get("/analytics?days=7").json()
    assert d["totals"]["calls"] == 0 and d["models"] == [] and len(d["daily"]) == 7
    assert all(x["input_tokens"] == 0 for x in d["daily"])


def test_analytics_takes_only_the_three_periods(api):
    assert api.get("/analytics?days=365").status_code == 422


def test_the_analytics_page_reads_only_its_route():
    block = UI[UI.index("const PANES = {"):]
    page = block[block.index("async analytics()"):]
    page = page[:page.index("\n  async ")]
    assert _calls(page) == {("GET", "/analytics")}
    assert 'data-pane="analytics"' in (ROOT / "src" / "agent" / "control" / "ui.html").read_text(encoding="utf-8")
