"""Audit probe: what D70's os.stat per path costs a walk.

    python docs/evidence/audit-iteration-3b/probe_g_is_protected_cost.py [SRC_DIR]

SRC_DIR defaults to this checkout's src/; pass an extracted e13eef5 tree to
compare (`git archive e13eef5 src tests/conftest.py | tar -x -C <dir>`).
Times grep over 2000 files (twice: cold, then inside the 2 s cache bucket) and
is_protected alone on 2000 distinct paths. Scratch state only.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[3]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "src"
TMP = Path(tempfile.mkdtemp(prefix="a3b-walk-"))
os.environ["ALPHA_DATA_DIR"] = str(TMP / "data")
os.environ["ALPHA_CONFIG_DIR"] = str(TMP / "config")
sys.path[:0] = [str(SRC), str(SRC.parent / "tests")]
import platformdirs  # noqa: E402
platformdirs.user_data_dir = lambda *a, **k: str(TMP / "pd")

from conftest import make_config  # noqa: E402

import agent.execute.tools as tools  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.security import capabilities as cap  # noqa: E402

(TMP / "p" / "work").mkdir(parents=True)
files = []
for i in range(2000):
    d = TMP / "p" / "work" / f"d{i // 100}"
    d.mkdir(exist_ok=True)
    f = d / f"f{i}.txt"
    f.write_text(f"line {i}\nneedle {i}\n", encoding="utf-8")
    files.append(f)
ctx = AgentContext(make_config(TMP / "p"))
task = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                   Budget(usd=1.0, seconds=60))
state = cap.task_state_paths(ctx, task)
time.sleep(2.1)                                   # a fresh cache bucket
t0 = time.perf_counter()
n = len(tools.tool_grep(ctx, task, "needle", "work", "**/*.txt")["matches"])
cold = time.perf_counter() - t0
t0 = time.perf_counter()
tools.tool_grep(ctx, task, "needle", "work", "**/*.txt")
warm = time.perf_counter() - t0
time.sleep(2.1)
t0 = time.perf_counter()
for f in files:
    cap.is_protected(str(f), ctx.config.root, state)
per = (time.perf_counter() - t0) / len(files)
print(f"source {SRC.parent.name}/{SRC.name}: grep 2000 files ({n} matches) cold {cold:.2f}s, "
      f"same bucket {warm:.2f}s; is_protected cold {per * 1e6:.0f} us/path")
ctx.close()
