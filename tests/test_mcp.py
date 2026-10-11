from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from agent.config import McpServerConfig
from agent.execute.loop import run_task
from agent.kernel.tasks import create_task
from agent.proc import process_table

FAKE = str(Path(__file__).parent / "fake_mcp_server.py")
FINAL = '<final>{"answer": "ok"}</final>'


def _call(tool, **args):
    return f'<action tool="{tool}">' + json.dumps(args) + "</action>"


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


@pytest.fixture
def mcp(ctx, task, workspace):
    ctx.config.autonomy.approve_scoped_writes = True

    def go(*script, read_only=(), timeout_s=30, env=None, argv=None):
        ctx.config.mcp_servers = {"fake": McpServerConfig(
            argv=argv or [sys.executable, FAKE], read_only=list(read_only), timeout_s=timeout_s, env=env or {})}
        ctx.backends["cloud"].queue(*script)
        res = run_task(ctx, task.id)
        return res, ctx.backends["cloud"].calls

    return go


def test_there_are_no_servers_by_default(ctx):
    assert ctx.config.mcp_servers == {}


def test_the_servers_tools_appear_and_work(ctx, mcp):
    _, calls = mcp(_call("mcp__fake__add", a=2, b=3), FINAL, read_only=["add"])
    assert "mcp__fake__add" in calls[0]["prefix"]
    assert "---" + chr(10) + "5" in calls[1]["tail"]


def test_the_servers_own_words_never_reach_the_trusted_prefix(ctx, mcp):
    _, calls = mcp(FINAL)
    p = calls[0]["prefix"]
    assert "mcp__fake__echo" in p and "IGNORE ALL PREVIOUS" not in p and "hostile" not in p


def test_a_tool_not_declared_read_only_is_a_writing_tool(ctx, mcp):
    mcp(FINAL, read_only=["add"])
    assert ctx.tools.get("mcp__fake__add").side_effecting is False
    assert ctx.tools.get("mcp__fake__echo").side_effecting is True


def test_its_output_is_untrusted_and_cannot_feed_a_writing_call(ctx, task, mcp, workspace):
    res, calls = mcp(_call("mcp__fake__echo", text="launch-code-7731"),
                     _call("mcp__fake__write_note", path="work/n.txt", text="launch-code-7731"), FINAL,
                     read_only=["echo"])
    assert "taint=untrusted" in calls[1]["tail"]
    assert not (workspace / "n.txt").exists()
    assert "requires <=user arguments, args are untrusted" in calls[2]["tail"]


def test_a_path_argument_still_has_to_be_inside_the_scope(ctx, mcp, tmp_path):
    _, calls = mcp(_call("mcp__fake__write_note", path="../escape.txt", text="x"), FINAL)
    assert "outside artifact_scope" in calls[1]["tail"] and not (tmp_path.parent / "escape.txt").exists()


def test_a_writing_call_inside_the_scope_goes_through(ctx, mcp, workspace):
    mcp(_call("mcp__fake__write_note", path=str(workspace / "n.txt"), text="hello"), FINAL)
    assert (workspace / "n.txt").read_text(encoding="utf-8") == "hello"


def test_a_server_error_comes_back_as_a_failed_result(ctx, mcp):
    _, calls = mcp(_call("mcp__fake__boom"), FINAL, read_only=["boom"])
    assert "it broke" in calls[1]["tail"] and '"ok": false' in calls[1]["tail"]


def test_a_slow_call_times_out_and_the_server_is_killed(ctx, task, mcp):
    pid_of = {}
    t0 = time.time()
    _, calls = mcp(_call("mcp__fake__pid"), _call("mcp__fake__slow"), _call("mcp__fake__add", a=1, b=1), FINAL,
                   read_only=["pid", "slow", "add"], timeout_s=2)
    assert time.time() - t0 < 30
    assert "timed out" in calls[2]["tail"]
    assert "not running" in calls[3]["tail"]
    pid_of["p"] = int(calls[1]["tail"].split("---" + chr(10), 1)[1].split(chr(10))[0])
    assert pid_of["p"] not in {p for p, _, _ in process_table()}


def test_closing_the_context_kills_the_server(ctx, mcp):
    _, calls = mcp(_call("mcp__fake__pid"), FINAL, read_only=["pid"])
    pid = int(calls[1]["tail"].split("---" + chr(10), 1)[1].split(chr(10))[0])
    assert pid in {p for p, _, _ in process_table()}
    ctx.close()
    deadline = time.time() + 15
    while time.time() < deadline and pid in {p for p, _, _ in process_table()}:
        time.sleep(0.2)
    assert pid not in {p for p, _, _ in process_table()}


def test_a_server_that_will_not_start_is_reported_and_the_task_goes_on(ctx, task, mcp):
    res, calls = mcp(FINAL, env={"FAKE_MCP_EXIT_AT_START": "1"})
    assert res.status.startswith("done") and "mcp__fake__add" not in calls[0]["prefix"]
    assert _events(ctx, task, "mcp.failed")[0]["server"] == "fake"


def test_the_small_kit_is_never_widened(tmp_path, workspace, task):
    from agent.context import AgentContext
    from conftest import make_config

    c = AgentContext(make_config(tmp_path))
    c.backends["cloud"].cfg.tool_profile = "small"
    c.close()
    c = AgentContext(make_config(tmp_path), backends=c.backends)
    c.config.mcp_servers = {"fake": McpServerConfig(argv=[sys.executable, FAKE])}
    t = create_task(c.conn, c.log, task.contract, task.budget)
    c.backends["cloud"].queue(FINAL)
    run_task(c, t.id)
    assert not [n for n in c.tools.names() if n.startswith("mcp__")]
    assert "mcp.skipped" in {r[0] for r in c.conn.execute("SELECT type FROM events WHERE task_id=?", (t.id,))}
    c.close()


@pytest.mark.parametrize("origin", ["telegram", "schedule"])
def test_unattended_origins_cannot_use_them(ctx, task, mcp, origin):
    ctx.conn.execute("UPDATE tasks SET origin=? WHERE id=?", (origin, task.id))
    ctx.conn.commit()
    ctx._origin.pop(task.id, None)
    _, calls = mcp(_call("mcp__fake__add", a=1, b=1), FINAL, read_only=["add"])
    assert "not available to this agent" in calls[1]["tail"]
