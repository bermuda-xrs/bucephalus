"""Audit probe: what D72's cached sweep costs while a task is RUNNING.

    python docs/evidence/audit-iteration-3b/probe_d_poll_during_run.py

Scratch state only (temp dir; ALPHA_DATA_DIR/ALPHA_CONFIG_DIR set before the
agent is imported). Builds N tasks x 60 events as the iteration-3 audit's
probe_f did, then times integrity_findings: a cache hit (nothing changed), and
a poll after ONE event append through the server's own connection - which is
what every step of an in-process run does between two 6 s polls.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="alpha-audit3b-poll-"))
os.environ["ALPHA_DATA_DIR"] = str(TMP / "data")
os.environ["ALPHA_CONFIG_DIR"] = str(TMP / "config")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import platformdirs  # noqa: E402
platformdirs.user_data_dir = lambda *a, **k: str(TMP / "pd")

from conftest import make_config  # noqa: E402

from agent.context import AgentContext  # noqa: E402
from agent.control.panels import integrity_findings  # noqa: E402
from agent.kernel.integrity import checkpoint  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402

EVENTS = 60


def build(n: int) -> tuple[AgentContext, str]:
    d = TMP / f"db{n}"
    (d / "work").mkdir(parents=True)
    ctx = AgentContext(make_config(d))
    ctx.conn.execute("PRAGMA synchronous = OFF")
    last = None
    for i in range(n):
        t = create_task(ctx.conn, ctx.log, TaskContract(goal=f"goal {i}", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=60))
        for k in range(EVENTS - 2):
            ctx.log.append(t.id, "model.called", {"purpose": "step", "k": k, "text": "x" * 400})
        checkpoint(ctx.log, ctx.anchor, t.id)
        last = t.id
    ctx.conn.execute("PRAGMA synchronous = FULL")
    return ctx, last


def timed(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def main() -> None:
    print(f"{'tasks':>6} {'first sweep':>12} {'idle poll (hit)':>16} {'poll after 1 append':>20}")
    for n in (100, 300, 1000):
        ctx, running = build(n)
        first = timed(lambda: integrity_findings(ctx))
        hit = min(timed(lambda: integrity_findings(ctx)) for _ in range(3))
        misses = []
        for _ in range(3):
            ctx.log.append(running, "model.called", {"purpose": "step", "text": "a running task's step"})
            misses.append(timed(lambda: integrity_findings(ctx)))
        print(f"{n:>6} {first:>11.2f}s {hit * 1000:>14.2f}ms {min(misses):>19.2f}s")
        ctx.close()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
