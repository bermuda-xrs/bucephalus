from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent.config import HooksConfig, McpServerConfig
from agent.execute.loop import run_task

PY = os.environ.get("BUCEPHALUS_REAL_MCP_PYTHON")
pytestmark = pytest.mark.skipif(
    not PY, reason="set BUCEPHALUS_REAL_MCP_PYTHON to a python with mcp-server-time and mcp-server-git installed")

FINAL = '<final>{"answer": "ok"}</final>'


def _call(tool, **args):
    return f'<action tool="{tool}">' + json.dumps(args) + "</action>"


@pytest.fixture
def hooked(ctx, task, workspace, tmp_path):
    ctx.config.autonomy.approve_scoped_writes = True
    repo = workspace / "repo"
    repo.mkdir()
    for cmd in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@t"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True)  # noqa: S603,S607
    (repo / "a.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)  # noqa: S603,S607
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "first"], check=True, capture_output=True)  # noqa: S603,S607
    hook = tmp_path / "block.py"
    hook.write_text(
        "import json, sys\nd = json.load(sys.stdin)\n"
        "if 'FORBIDDEN-PATTERN' in json.dumps(d['args']):\n    print('blocked: FORBIDDEN-PATTERN'); sys.exit(1)\n",
        encoding="utf-8")
    ctx.config.hooks = HooksConfig.model_validate({"pre_tool": [{"argv": [sys.executable, str(hook)], "tools": ["write_file"]}]})
    ctx.config.mcp_servers = {
        "time": McpServerConfig(argv=[PY, "-m", "mcp_server_time"], read_only=["get_current_time", "convert_time"],
                                start_timeout_s=60),
        "git": McpServerConfig(argv=[PY, "-m", "mcp_server_git", "--repository", str(repo)],
                               read_only=["git_status", "git_log", "git_diff", "git_show", "git_branch"], start_timeout_s=60),
    }
    return ctx, repo


def test_a_task_uses_the_real_time_and_git_servers_and_the_hook_blocks_one_pattern(hooked, workspace):
    ctx, repo = hooked
    ctx.backends["cloud"].queue(
        _call("mcp__time__get_current_time", timezone="Europe/London"),
        _call("mcp__git__git_status", repo_path=str(repo)),
        _call("mcp__git__git_log", repo_path=str(repo), max_count=1),
        _call("write_file", path="work/notes.txt", content="a perfectly fine note"),
        _call("write_file", path="work/bad.txt", content="has FORBIDDEN-PATTERN inside"),
        FINAL)
    res = run_task(ctx, hooked[0].conn.execute("SELECT id FROM tasks").fetchone()[0])
    assert res.status.startswith("done")
    tails = [c["tail"] for c in ctx.backends["cloud"].calls]
    assert "mcp__time__get_current_time" in ctx.backends["cloud"].calls[0]["prefix"]
    assert '"timezone": "Europe/London"' in tails[1] and "taint=untrusted" in tails[1]
    assert "nothing to commit" in tails[2] and "first" in tails[3]
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "a perfectly fine note"
    assert not (workspace / "bad.txt").exists() and "hook refused write_file" in tails[5] and "FORBIDDEN-PATTERN" in tails[5]


def test_a_git_write_is_not_a_read_and_still_has_to_pass_the_gate(hooked):
    ctx, repo = hooked
    ctx.config.autonomy.approve_scoped_writes = False
    ctx.config.autonomy.auto_review = False
    (repo / "b.txt").write_text("new", encoding="utf-8")
    ctx.backends["cloud"].queue(_call("mcp__git__git_add", repo_path=str(repo), files=["b.txt"]), FINAL)
    res = run_task(ctx, ctx.conn.execute("SELECT id FROM tasks").fetchone()[0])
    assert res.status == "paused", "git_add is a writing tool: it waits for a human"
