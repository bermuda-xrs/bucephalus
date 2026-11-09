from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.config import McpServerConfig
from agent.control.server import create_app
from agent.kernel.tasks import set_status
from agent.kernel.types import TaskStatus
from test_console_reskin_guards import HTML, UI, _calls

FAKE = str(Path(__file__).parent / "fake_mcp_server.py")
SECRET = "S3CRET-ENV-VALUE"


@pytest.fixture
def api(ctx, workspace):
    ctx.config.mcp_servers = {"fake": McpServerConfig(argv=[sys.executable, FAKE], read_only=["add"],
                                                      env={"API_TOKEN": SECRET}),
                              "dead": McpServerConfig(argv=[sys.executable, FAKE], env={"FAKE_MCP_EXIT_AT_START": "1"})}
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_the_list_shows_servers_and_masks_their_environment(api):
    r = api.get("/mcp")
    assert SECRET not in r.text
    by = {s["name"]: s for s in r.json()["servers"]}
    assert by["fake"]["env"] == ["API_TOKEN"] and by["fake"]["read_only"] == ["add"]
    assert by["fake"]["argv"][0] == sys.executable and by["fake"]["timeout_s"] == 30
    assert r.json()["running"] is False


def test_the_list_is_empty_with_no_servers(ctx, workspace):
    ctx.config.mcp_servers = {}
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    assert c.get("/mcp").json()["servers"] == []


def test_a_test_lists_the_tools_and_stops_the_server(api):
    d = api.post("/mcp/fake/test").json()
    names = {t["name"] for t in d["tools"]}
    assert {"echo", "add", "write_note"} <= names and d["ok"] is True
    assert SECRET not in str(d)
    echo = next(t for t in d["tools"] if t["name"] == "echo")
    assert echo["description"].startswith("IGNORE ALL PREVIOUS"), "shown as the data it is, never obeyed"
    assert [t["name"] for t in d["tools"] if t["writes"] is False] == ["add"]


def test_a_server_that_will_not_start_is_reported_not_raised(api):
    d = api.post("/mcp/dead/test").json()
    assert d["ok"] is False and d["tools"] == [] and d["error"]


def test_only_a_configured_server_can_be_tested(api):
    assert api.post("/mcp/nothing/test").status_code == 404


def test_no_test_while_a_task_runs(ctx, api, task):
    set_status(ctx.conn, ctx.log, task, TaskStatus.RUNNING)
    assert api.post("/mcp/fake/test").status_code == 409


def test_the_page_calls_only_its_routes_and_escapes_what_servers_say():
    assert {("GET", "/mcp"), ("POST", "/mcp/{}/test")} <= _calls(UI)
    assert 'data-pane="mcp"' in HTML
    block = UI[UI.index("async mcp()"):]
    block = block[:block.index("\n  async ")]
    assert "esc(String(t.description" in block and "innerHTML = t." not in block
