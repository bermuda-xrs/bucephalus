from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from agent.config import McpServerConfig
from agent.execute.loop import run_task

HERE = Path(__file__).parent
FAKE = str(HERE / "fake_mcp_server.py")
SCRIPTED = str(HERE / "scripted_mcp_server.py")
FINAL = '<final>{"answer": "ok"}</final>'
LEAKY = {"DATABASE_URL": "postgres://u:pw@h/db", "SSH_AUTH_SOCK": "/tmp/agent.sock", "GH_PAT": "x", "AWS_PROFILE": "p"}


def _call(tool, **a):
    return f'<action tool="{tool}">' + json.dumps(a) + "</action>"


@pytest.fixture
def leaky(monkeypatch):
    for k, v in LEAKY.items():
        monkeypatch.setenv(k, v)


def test_a_hook_gets_only_allowlisted_variables_and_its_own(tmp_path, leaky):
    from agent.execute.hooks import _run_one

    hook = tmp_path / "env.py"
    hook.write_text("import os; print(','.join(sorted(os.environ)))", encoding="utf-8")
    code, text = _run_one(types.SimpleNamespace(argv=[sys.executable, str(hook)], timeout_s=20, env={"HOOK_ONLY": "1"}),
                          {}, str(tmp_path))
    keys = {k.upper() for k in text.split(",")}
    assert code == 0 and "PATH" in keys and "HOOK_ONLY" in keys
    assert not keys & set(LEAKY), keys & set(LEAKY)


def test_an_mcp_server_gets_only_allowlisted_variables_and_its_own(tmp_path, leaky):
    from agent.execute.mcp import McpServer

    s = McpServer("s", McpServerConfig(argv=[sys.executable, SCRIPTED],
                                       env={"MCP_SCRIPT": json.dumps({"echo_env": True}), "SERVER_ONLY": "1"}), str(tmp_path))
    try:
        s.start()
        keys = {k.upper() for k in s.call("t", {})["content"].split(",")}
    finally:
        s.close()
    assert "PATH" in keys and "SERVER_ONLY" in keys and not keys & set(LEAKY)


def test_a_python_hook_runs_isolated():
    from agent.execute.hooks import _isolated

    assert _isolated(["python", "h.py"]) == ["python", "-I", "h.py"]
    assert _isolated([r"C:\Py\python3.11.exe", "-m", "x"]) == [r"C:\Py\python3.11.exe", "-I", "-m", "x"]
    assert _isolated(["python", "-I", "h.py"]) == ["python", "-I", "h.py"]
    assert _isolated(["node", "h.js"]) == ["node", "h.js"]


def test_a_hook_cannot_import_its_neighbours_or_shadow_the_stdlib(tmp_path):
    from agent.execute.hooks import _run_one

    (tmp_path / "helper.py").write_text("X = 1", encoding="utf-8")
    (tmp_path / "json.py").write_text("print('SHADOWED')", encoding="utf-8")
    hook = tmp_path / "hook.py"
    hook.write_text("import json\ntry:\n    import helper\n    print('NEIGHBOUR')\nexcept ImportError:\n    print('isolated')\n",
                    encoding="utf-8")
    code, text = _run_one(types.SimpleNamespace(argv=[sys.executable, str(hook)], timeout_s=20), {}, str(tmp_path))
    assert code == 0 and text == "isolated", text


def test_the_diff_view_never_writes_a_checkpoint(ctx, task, workspace):
    from agent.execute import shadow
    from agent.execute.diffview import task_diffs
    from agent.execute.shadow import ShadowRepo
    from agent.kernel.tasks import load_task

    if not shadow.git_available():
        pytest.skip("git is not installed")
    ctx.config.mechanisms.shadow_checkpoints = True
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.backends["cloud"].queue(_call("write_file", path="work/a.txt", content="one\n"), FINAL)
    run_task(ctx, task.id)
    (workspace / "late.txt").write_text("after the run\n", encoding="utf-8")
    repo = ShadowRepo.for_task(ctx, load_task(ctx.conn, task.id))
    before = repo.log()
    d = task_diffs(ctx, task.id)
    assert repo.log() == before and "work/late.txt" in d[-1]["files"] and "+after the run" in d[-1]["diff"]


@pytest.fixture
def mcp(ctx, task, workspace):
    ctx.config.autonomy.approve_scoped_writes = True

    def go(*script, path_args=None, read_only=()):
        ctx.config.mcp_servers = {"fake": McpServerConfig(argv=[sys.executable, FAKE], read_only=list(read_only),
                                                          path_args=path_args or {})}
        ctx.backends["cloud"].queue(*script)
        return run_task(ctx, task.id), ctx.backends["cloud"].calls

    return go


def test_a_bare_name_under_any_key_of_a_writing_tool_is_checked(mcp):
    _, calls = mcp(_call("mcp__fake__where", repo_path="work", note="escaped"), FINAL)
    assert "DENIED by the capability gate" in calls[1]["tail"] and "at:" not in calls[1]["tail"]


def test_a_writing_tool_with_undeclared_args_parks_for_approval(mcp):
    res, _ = mcp(_call("mcp__fake__where", repo_path="work", note="work/x"), FINAL)
    assert res.status == "paused" and res.approval_id


def test_a_declared_tool_checks_its_paths_and_leaves_its_text_alone(mcp, tmp_path):
    res, calls = mcp(_call("mcp__fake__where", repo_path="work", note="hello"), FINAL, path_args={"where": ["repo_path"]})
    assert res.status.startswith("done") and "at:work" in calls[1]["tail"]


def test_a_declared_path_outside_the_scope_is_still_refused(mcp, tmp_path):
    _, calls = mcp(_call("mcp__fake__where", repo_path=str(tmp_path.parent), note="hello"), FINAL,
                   path_args={"where": ["repo_path"]})
    assert "DENIED by the capability gate" in calls[1]["tail"]
