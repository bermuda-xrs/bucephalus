"""Audit probe: what the console's 6-second poll costs against a large database.

    python docs/evidence/audit-iteration-3/probe_f_poll_cost.py

Scratch state only: everything lives in a temp directory, and ALPHA_DATA_DIR /
ALPHA_CONFIG_DIR are pointed at it before the agent is imported. Builds
databases of N tasks x 60 events (payloads ~400 bytes, a checkpoint and a plan
pin per task in the anchor, 5 tasks per conversation), then times the three
requests ui.js makes every 6 s (loadAlerts -> /alerts, loadSide ->
/conversations + /approvals) through the real app, plus verify_all alone.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="alpha-audit-poll-"))
os.environ["ALPHA_DATA_DIR"] = str(TMP / "data")
os.environ["ALPHA_CONFIG_DIR"] = str(TMP / "config")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import make_config  # noqa: E402  (tests/conftest.py; its fixtures are not used)
from fastapi.testclient import TestClient  # noqa: E402

from agent.context import AgentContext  # noqa: E402
from agent.control import conversations as conv  # noqa: E402
from agent.control.server import create_app  # noqa: E402
from agent.kernel.integrity import checkpoint, verify_all  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402

EVENTS = 60


def build(n_tasks: int) -> AgentContext:
    d = TMP / f"db{n_tasks}"
    (d / "work").mkdir(parents=True)
    ctx = AgentContext(make_config(d))
    ctx.conn.execute("PRAGMA synchronous = OFF")            # building only
    cid = None
    for i in range(n_tasks):
        if i % 5 == 0:
            cid = conv.create(ctx.conn, f"conversation {i}")["id"]
        t = create_task(ctx.conn, ctx.log, TaskContract(goal=f"goal {i}", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=60))
        ctx.conn.execute("INSERT INTO conversation_tasks(task_id, conversation_id, position) VALUES (?,?,?)",
                         (t.id, cid, i % 5))
        for k in range(EVENTS - 2):
            ctx.log.append(t.id, "model.called", {"purpose": "step", "est_input_tokens": 900 + k,
                                                  "text": "x" * 400})
        checkpoint(ctx.log, ctx.anchor, t.id)
        ctx.anchor.record(f"plan:{t.id}", "p" * 64)
    ctx.conn.execute("PRAGMA synchronous = FULL")
    return ctx


def timed(fn, reps=3) -> float:
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def main() -> None:
    print(f"{'tasks':>6} {'events':>7} {'anchor lines':>12} {'verify_all':>11} {'/alerts':>9} "
          f"{'/conversations':>15} {'/approvals':>11} {'poll total':>11} {'share of 6 s':>12}")
    for n in (100, 300, 1000):
        t0 = time.time()
        ctx = build(n)
        built = time.time() - t0
        app = create_app(ctx)
        c = TestClient(app, base_url="http://127.0.0.1")
        c.headers["Authorization"] = "Bearer " + app.state.token
        va = timed(lambda: verify_all(ctx.conn, ctx.log, ctx.anchor, ctx.memory))
        al = timed(lambda: c.get("/alerts").raise_for_status())
        cv = timed(lambda: c.get("/conversations?archived=false&q=").raise_for_status())
        ap = timed(lambda: c.get("/approvals").raise_for_status())
        total = al + cv + ap
        lines = len(Path(ctx.anchor.path).read_text(encoding="utf-8").splitlines())
        print(f"{n:>6} {n * EVENTS:>7} {lines:>12} {va:>10.2f}s {al:>8.2f}s {cv:>14.3f}s "
              f"{ap:>10.3f}s {total:>10.2f}s {total / 6:>11.0%}   (built in {built:.0f}s)")
        ctx.close()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
