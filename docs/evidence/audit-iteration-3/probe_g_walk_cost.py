"""Audit probe: the cost of D61's per-file check in grep and glob.

    python docs/evidence/audit-iteration-3/probe_g_walk_cost.py

`_reachable` (execute/tools.py) calls task_state_paths(ctx, task) and two
realpath()s for EVERY file a walk reaches. task_state_paths re-globs the
project's config*.yaml, stats each, and rebuilds the whole tuple each time.
Scratch state only.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="alpha-audit-walk-"))
os.environ["ALPHA_DATA_DIR"] = str(TMP / "data")
os.environ["ALPHA_CONFIG_DIR"] = str(TMP / "config")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import make_config  # noqa: E402

import agent.execute.tools as tools  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.security import capabilities as cap  # noqa: E402


def main() -> None:
    (TMP / "p" / "work").mkdir(parents=True)
    for i in range(2000):
        d = TMP / "p" / "work" / f"d{i // 100}"
        d.mkdir(exist_ok=True)
        (d / f"f{i}.txt").write_text(f"line {i}\nneedle {i}\n", encoding="utf-8")
    ctx = AgentContext(make_config(TMP / "p"))
    task = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=60))

    def run():
        t0 = time.perf_counter()
        r = tools.tool_grep(ctx, task, "needle", "work", "**/*.txt")
        return time.perf_counter() - t0, len(r["matches"])

    with_check = min(run() for _ in range(3))
    real = tools._reachable
    tools._reachable = lambda ctx, task, f: True
    without = min(run() for _ in range(3))
    tools._reachable = real
    t0 = time.perf_counter()
    for _ in range(2000):
        cap.task_state_paths(ctx, task)
    tsp = (time.perf_counter() - t0) / 2000
    print(f"grep over 2000 files: {with_check[0]:.2f}s with _reachable, {without[0]:.2f}s without "
          f"({with_check[0] / without[0]:.1f}x); task_state_paths alone: {tsp * 1000:.2f} ms per call")

    # The console's file browser: one listing judges every child with _allowed.
    from fastapi.testclient import TestClient

    from agent.control.server import create_app
    flat = TMP / "p" / "work" / "flat"
    flat.mkdir()
    for i in range(2000):
        (flat / f"f{i}.txt").write_text("x", encoding="utf-8")
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    t0 = time.perf_counter()
    r = c.get(f"/tasks/{task.id}/files", params={"path": "work/flat"})
    listing = time.perf_counter() - t0
    t0 = time.perf_counter()
    os.listdir(flat)
    plain = time.perf_counter() - t0
    print(f"GET /tasks/{{id}}/files on a 2000-entry directory: {listing:.2f}s "
          f"({len(r.json()['entries'])} entries; os.listdir alone {plain * 1000:.1f} ms)")
    ctx.close()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
