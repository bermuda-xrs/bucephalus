from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent.config import McpServerConfig
from agent.execute import mcp
from agent.execute.mcp import McpError, McpServer, _schema, attach

SCRIPTED = str(Path(__file__).parent / "scripted_mcp_server.py")


@pytest.fixture
def server(tmp_path):
    made = []

    def go(**script):
        cfg = McpServerConfig(argv=[sys.executable, SCRIPTED], env={"MCP_SCRIPT": json.dumps(script)},
                              timeout_s=10, start_timeout_s=10)
        s = McpServer("s", cfg, str(tmp_path))
        made.append(s)
        return s

    yield go
    for s in made:
        s.close()


@pytest.mark.parametrize("t", ["string", "number", "integer", "boolean", "array", "object", "null"])
def test_every_json_schema_type_is_kept(t):
    assert _schema({"properties": {"x": {"type": t}}})["properties"]["x"]["type"] == t


@pytest.mark.parametrize("t", ["str", "file", ["string"], None])
def test_anything_else_becomes_a_string(t):
    assert _schema({"properties": {"x": {"type": t}}})["properties"]["x"] == {"type": "string"}


def test_an_enum_of_plain_values_is_kept_up_to_fifty():
    vals = [True, 1, 1.5, "a" * 64] + [f"v{i}" for i in range(60)]
    assert _schema({"properties": {"x": {"enum": vals}}})["properties"]["x"]["enum"] == vals[:50]


@pytest.mark.parametrize("bad", [{"a": 1}, "a" * 65, "tab\there", None, ["x"]])
def test_an_enum_with_anything_else_is_dropped(bad):
    assert "enum" not in _schema({"properties": {"x": {"enum": ["ok", bad]}}})["properties"]["x"]


def test_required_names_only_declared_arguments():
    assert _schema({"properties": {"a": {}}, "required": ["a", "ghost"]})["required"] == ["a"]
    assert _schema({"properties": {"a": {}}, "required": "a"})["required"] == []


def test_a_server_request_for_ping_is_answered_with_a_result(server):
    s = server(ask_first={"initialize": "ping"}, ask_wants="result")
    s.start()
    assert s.running


def test_any_other_server_request_gets_method_not_found(server):
    s = server(ask_first={"initialize": "sampling/createMessage"}, ask_wants="error")
    s.start()
    s.close()
    assert "-32601" in s.stderr_tail and "this client offers no sampling/createMessage" in s.stderr_tail


@pytest.mark.parametrize("err, want", [({"code": 1, "message": "M" * 400}, "M" * 300), ("plain words", "plain words")])
def test_a_server_error_becomes_its_message(server, err, want):
    s = server(raw={"initialize": {"error": err}})
    with pytest.raises(McpError) as e:
        s.start()
    assert str(e.value) == want
    assert not s.running


def test_a_result_that_is_not_an_object_is_refused(server):
    with pytest.raises(McpError, match="initialize: the server's result is not an object"):
        server(raw={"initialize": {"result": [1]}}).start()


def test_a_server_that_exits_says_so_with_its_stderr(server):
    s = server(exit_on=["tools/list"])
    s.start()
    with pytest.raises(McpError, match="the server exited: dying as scripted"):
        s.list_tools()


def test_a_request_to_a_closed_server_is_refused(server):
    s = server()
    s.start()
    s.close()
    assert not s.running
    with pytest.raises(McpError, match="not running"):
        s.request("tools/list", {}, 5)


def test_tools_must_be_a_list(server):
    s = server(raw={"tools/list": {"result": {"tools": {"a": 1}}}})
    s.start()
    with pytest.raises(McpError, match="tools is not a list"):
        s.list_tools()


def test_too_many_tools_is_refused(server, monkeypatch):
    monkeypatch.setattr(mcp, "MAX_TOOLS", 2)
    s = server(tools=[{"name": f"t{i}"} for i in range(3)])
    s.start()
    with pytest.raises(McpError, match="more than 2 tools"):
        s.list_tools()


def test_pages_are_followed_up_to_the_cap(server, monkeypatch):
    monkeypatch.setattr(mcp, "MAX_PAGES", 3)
    s = server(tools=[{"name": "a"}], pages=3)
    s.start()
    assert [t["name"] for t in s.list_tools()] == ["a"]
    s2 = server(tools=[{"name": "a"}], pages=4)
    s2.start()
    with pytest.raises(McpError, match="more than 3 pages"):
        s2.list_tools()


def test_a_call_result_is_text_capped_and_other_content_named(server, monkeypatch):
    monkeypatch.setattr(mcp, "MAX_RESULT_CHARS", 10)
    s = server(call={"content": [{"type": "image"}, {"type": "text", "text": "x" * 50}, "junk"]})
    s.start()
    assert s.call("t", {}) == {"ok": True, "content": "[image omitted]\nxx"[:10]}


def test_content_that_is_not_a_list_fails_the_call(server):
    s = server(call={"content": "text"})
    s.start()
    assert s.call("t", {}) == {"ok": False, "error": "the server's content is not a list"}


def test_a_call_to_a_dead_server_fails_the_call(server):
    s = server()
    s.start()
    s.close()
    r = s.call("t", {})
    assert r["ok"] is False and "not running" in r["error"]


def _attach(ctx, task, servers):
    ctx.config.mcp_servers = {name: McpServerConfig(argv=[sys.executable, SCRIPTED], env={"MCP_SCRIPT": json.dumps(s)})
                              for name, s in servers.items()}
    attach(ctx, task)
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type IN ('mcp.started', 'mcp.skipped') ORDER BY seq",
        (task.id,))]


def test_a_tool_name_that_would_be_too_long_or_unsafe_is_skipped(ctx, task):
    fits = "t" * (64 - len("mcp__s__"))
    ev = _attach(ctx, task, {"s": {"tools": [{"name": fits}, {"name": fits + "x"}, {"name": "has space"}]}})
    assert ctx.tools.get("mcp__s__" + fits) is not None
    assert [e["why"][:17] for e in ev if "why" in e] == ["tool name refused"] * 2
    assert ev[-1] == {"server": "s", "tools": 1}


def test_attach_runs_once_per_context(ctx, task):
    _attach(ctx, task, {"s": {"tools": [{"name": "a"}]}})
    attach(ctx, task)
    assert len(ctx.mcp_servers) == 1


def test_two_servers_whose_names_sanitize_alike_do_not_replace_each_other(ctx, task):
    ev = _attach(ctx, task, {"a-b": {"tools": [{"name": "t"}]}, "a_b": {"tools": [{"name": "t"}]}})
    assert [e.get("tools") for e in ev] == [1, 0]


def test_an_undeclared_argument_fails_the_call(ctx, task):
    _attach(ctx, task, {"s": {"tools": [{"name": "t", "inputSchema": {"properties": {"a": {}}}}]}})
    r = ctx.tools.get("mcp__s__t").fn(ctx, task, b=1)
    assert r == {"ok": False, "error": "t takes no argument named b; it takes a"}


def test_a_slow_list_is_waited_for_within_the_start_timeout(server):
    s = server(tools=[{"name": "a"}], list_delay_s=1.0)
    s.start()
    assert [t["name"] for t in s.list_tools()] == ["a"]


def test_a_server_that_cannot_start_says_so(tmp_path):
    s = McpServer("s", McpServerConfig(argv=[str(tmp_path / "no-such-server.exe")]), str(tmp_path))
    with pytest.raises(McpError, match="could not start"):
        s.start()
