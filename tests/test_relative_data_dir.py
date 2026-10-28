from __future__ import annotations

import os

import yaml

from agent.config import load_config
from agent.context import AgentContext
from agent.execute.tools import tool_glob, tool_read_file
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.security.capabilities import gate_tool_call


def test_a_relative_data_dir_finds_work_files_and_shows_paths_that_read_back(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BUCEPHALUS_DATA_DIR", os.path.join("runs", "x", "data"))
    (tmp_path / "c.yaml").write_text(yaml.safe_dump({
        "backends": {"cloud": {"kind": "mock", "model": "m"}, "review": {"kind": "mock", "model": "r"}}}),
        encoding="utf-8")
    cfg = load_config(tmp_path / "c.yaml")
    work = tmp_path / "runs" / "x" / "data" / "work"
    work.mkdir(parents=True, exist_ok=True)
    for name in ("a.json", "b.json", "notes.txt"):
        (work / name).write_text("{}", encoding="utf-8")
    ctx = AgentContext(cfg)
    try:
        t = create_task(ctx.conn, ctx.log, TaskContract(goal="count", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=300))
        assert tool_glob(ctx, t, "*.json", "work/")["matches"] == ["a.json", "b.json"]
        shown = tool_read_file(ctx, t, "work/a.json")["path"]
        assert gate_tool_call(ctx, t, ctx.tools.get("read_file"), {"path": shown}).allowed
        assert tool_read_file(ctx, t, shown)["ok"]
    finally:
        ctx.close()
