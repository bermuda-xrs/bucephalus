"""Audit probe: the capability gate on a path argument longer than Windows' 32,767.

    python docs/evidence/audit-iteration-3b/probe_e_long_path_gate.py [SRC_DIR]

SRC_DIR defaults to this checkout's src/. Pass an extracted older tree
(`git archive e13eef5 src tests/conftest.py | tar -x -C <dir>`, then
<dir>/src) to compare. Scratch state only.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[3]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "src"
T = Path(tempfile.mkdtemp(prefix="a3b-gate-"))
os.environ["ALPHA_DATA_DIR"] = str(T / "d")
os.environ["ALPHA_CONFIG_DIR"] = str(T / "c")
sys.path[:0] = [str(SRC), str(SRC.parent / "tests")]
import platformdirs  # noqa: E402
platformdirs.user_data_dir = lambda *a, **k: str(T / "pd")

from conftest import make_config  # noqa: E402

from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import execute_action  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.security.capabilities import gate_tool_call  # noqa: E402

(T / "work").mkdir()
ctx = AgentContext(make_config(T))
task = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                   Budget(usd=1, seconds=60))
path = "work/" + "b/" * 20000 + "c.txt"
print("source:", SRC)
for tool in ("read_file", "write_file"):
    args = {"path": path} if tool == "read_file" else {"path": path, "content": "x"}
    try:
        d = gate_tool_call(ctx, task, ctx.tools.get(tool), args)
        print(f"gate {tool}: {d.verdict} - {d.reason[:70]}")
    except Exception as e:  # noqa: BLE001
        print(f"gate {tool}: RAISED {type(e).__name__}: {e}")
ctx.close()
