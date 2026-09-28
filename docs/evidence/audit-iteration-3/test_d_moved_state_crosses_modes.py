"""Audit probe: iteration 3's part 1.1 moves a synced checkout's database and
anchor to <user data dir>/checkouts/<name>-<hash>. The user data dir is also
the ROOT of the installed mode (the desktop app: desktop.resolve_state sets
ALPHA_DATA_DIR to it). The installed mode's gate protects its own state by
name and by configured path - and `checkouts/` is neither. A desktop task
whose scope is its root (["."]) reads, and can overwrite, the checkout's
database: every checkout task's goals, answers and tool output.

Scratch state only: platformdirs' user data dir is a tmp directory here.
"""

from __future__ import annotations

from pathlib import Path

from agent.config import load_config
from agent.context import AgentContext
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.security.capabilities import gate_tool_call, is_protected, task_state_paths

SECRET_GOAL = "CHECKOUT-TASK-GOAL-7781 quarterly salaries"


def _checkout(parent: Path) -> Path:
    root = parent / "OneDrive" / "proj"
    (root / "src" / "agent").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    real_cfg = Path(__file__).resolve().parents[3] / "config.yaml"
    (root / "config.yaml").write_text(real_cfg.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def test_FINDING_an_installed_mode_task_reads_a_checkouts_moved_database(tmp_path, monkeypatch):
    from conftest import make_config

    data = tmp_path / "user_data"                       # stands for %LOCALAPPDATA%\alpha-test-1\...
    monkeypatch.delenv("ALPHA_DATA_DIR", raising=False)
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.setattr("platformdirs.user_data_dir", lambda *a, **k: str(data))

    # 1. A developer runs the checkout, which sits in a synced folder.
    cfg = load_config(_checkout(tmp_path) / "config.yaml")
    assert cfg.state_dir and Path(cfg.state_dir).parent == data / "checkouts", cfg.state_dir
    dev = AgentContext(cfg)
    create_task(dev.conn, dev.log, TaskContract(goal=SECRET_GOAL, artifact_scope=["work"]),
                Budget(usd=1.0, seconds=60))
    dev.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    dev.close()
    moved_db = Path(cfg.state_dir) / Path(cfg.db_path).name
    assert moved_db.exists()

    # 2. The desktop app on the same machine: installed mode, root = user data dir.
    monkeypatch.setenv("ALPHA_DATA_DIR", str(data))
    app_cfg = make_config(data)
    app = AgentContext(app_cfg)
    try:
        task = create_task(app.conn, app.log, TaskContract(goal="tidy my files", artifact_scope=["."]),
                           Budget(usd=1.0, seconds=60))
        rel = moved_db.relative_to(data).as_posix()
        print("checkout db, relative to the desktop root:", rel)
        print("is_protected under the desktop's state:",
              is_protected(rel, str(data), task_state_paths(app, task)))
        read = app.tools.get("read_file")
        d = gate_tool_call(app, task, read, {"path": rel})
        print("gate on read_file:", d.verdict, d.reason)
        assert d.allowed
        from agent.execute.tools import tool_read_file
        out = tool_read_file(app, task, rel)
        assert SECRET_GOAL in out["content"]
        print("read_file returned the checkout's task goal:", SECRET_GOAL in out["content"])

        write = app.tools.get("write_file")
        dw = gate_tool_call(app, task, write, {"path": rel, "content": "x"})
        print("gate on write_file over it:", dw.verdict, "-", dw.reason[:90])
        assert "protected" not in dw.reason and "outside" not in dw.reason
    finally:
        app.close()
