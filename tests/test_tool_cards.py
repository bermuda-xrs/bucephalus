from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app
from agent.execute.loop import run_task

CONTROL = Path(__file__).resolve().parents[1] / "src" / "agent" / "control"
FINAL = '<final>{"answer": "ok"}</final>'


def _act(tool, **args):
    return f'<action tool="{tool}">{json.dumps(args)}</action>'


@pytest.fixture
def api(ctx, task, workspace):
    (workspace / "a.txt").write_text("hello from the file", encoding="utf-8")
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.backends["cloud"].queue(_act("read_file", path="work/a.txt"), _act("write_file", path="work/b.txt", content="written"),
                                _act("list_dir", path="work"), FINAL)
    run_task(ctx, task.id)
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c, task, ctx


def _keys(ctx, task):
    return [json.loads(r[0])["key"] for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='tool.intent' ORDER BY seq", (task.id,))]


def test_a_call_can_be_opened_with_its_arguments_and_result(api):
    c, task, ctx = api
    k = _keys(ctx, task)
    got = c.get(f"/tasks/{task.id}/call", params={"key": k[0]}).json()
    assert got["tool"] == "read_file" and got["args"] == {"path": "work/a.txt"}
    assert "hello from the file" in got["result"] and got["taint"] == "untrusted" and got["ok"] is True


def test_each_call_gets_its_own_result_not_a_neighbours(api):
    c, task, ctx = api
    k = _keys(ctx, task)
    w = c.get(f"/tasks/{task.id}/call", params={"key": k[1]}).json()
    d = c.get(f"/tasks/{task.id}/call", params={"key": k[2]}).json()
    assert w["tool"] == "write_file" and w["args"]["content"] == "written" and "hello from the file" not in w["result"]
    assert d["tool"] == "list_dir" and "a.txt" in d["result"] and "written" not in d["args"].values()


def test_a_long_result_is_cut_and_says_so(ctx, task, workspace):
    (workspace / "big.txt").write_text("x" * 20000, encoding="utf-8")
    ctx.backends["cloud"].queue(_act("read_file", path="work/big.txt"), FINAL)
    run_task(ctx, task.id)
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    got = c.get(f"/tasks/{task.id}/call", params={"key": _keys(ctx, task)[0]}).json()
    assert len(got["result"]) <= 6000 and got["truncated"] is True


def test_an_unknown_key_is_a_404_and_the_route_needs_the_token(api):
    c, task, _ = api
    assert c.get(f"/tasks/{task.id}/call", params={"key": "nope"}).status_code == 404
    bare = TestClient(c.app, base_url="http://127.0.0.1")
    assert bare.get(f"/tasks/{task.id}/call", params={"key": "x"}).status_code in (401, 403)


def test_the_console_cards_open_and_escape_what_they_show():
    js = (CONTROL / "ui.js").read_text(encoding="utf-8")
    assert "toggleTool" in js and "/call?key=" in js
    body = js[js.index("function toolDetail"):js.index("function toolCard")] if "function toolDetail" in js else ""
    assert "esc(" in body, "a tool result is untrusted text and must be escaped where it is shown"


def test_a_finished_task_lists_its_calls_for_cards_after_a_reload(api):
    c, task, ctx = api
    got = c.get(f"/tasks/{task.id}/calls").json()
    assert [x["tool"] for x in got] == ["read_file", "write_file", "list_dir"]
    assert all(x["ok"] is True and x["key"] and "preview" in x for x in got)
    assert got[0]["key"] == _keys(ctx, task)[0]


def test_the_console_loads_cards_for_finished_tasks_once():
    js = (CONTROL / "ui.js").read_text(encoding="utf-8")
    assert "/calls" in js and "S.callsLoaded" in js
