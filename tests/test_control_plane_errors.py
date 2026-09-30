"""The control plane's error paths: the audits' open lows, each red first
(2026-09-30). A 500 is where the security headers went missing, where a
credential crashed the guard, and where an unknown id said "server error"
instead of "not found".
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from agent.control.guard import SECURITY_HEADERS
from agent.control.server import create_app
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract


def _client(ctx, **kw):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1", **kw)
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_D47_a_non_ascii_credential_is_refused_not_a_crash(ctx):
    c = _client(ctx, raise_server_exceptions=False)
    # Bytes: the client library refuses a non-ASCII str header; a browser or
    # curl sends the bytes anyway, and the server reads them as latin-1.
    r = c.get("/approvals", headers={"Authorization": "Bearer café".encode()})
    assert r.status_code == 401, r.status_code
    r = c.get("/approvals", headers={"Authorization": b"", "X-Alpha-Token": "été".encode()})
    assert r.status_code == 401, r.status_code
    r = c.get("/approvals", params={"token": "été"}, headers={"Authorization": b""})
    assert r.status_code == 401, r.status_code


def test_D74_a_crashing_route_still_carries_the_security_headers(ctx, monkeypatch):
    from agent.control import panels

    def boom(*_a, **_k):
        raise RuntimeError("a route crashed")
    monkeypatch.setattr(panels, "logs", boom)
    r = _client(ctx, raise_server_exceptions=False).get("/panels/logs")
    assert r.status_code == 500
    for k, v in SECURITY_HEADERS.items():
        assert r.headers.get(k) == v, (k, dict(r.headers))
    assert "a route crashed" not in r.text          # no traceback to the client


@pytest.mark.parametrize("method,path,body", [
    ("post", "/tasks/t_nope/plan", None),
    ("post", "/tasks/t_nope/plan/approve", {"plan_id": "p", "edits": []}),
    ("post", "/tasks/t_nope/plan/reject", {"plan_id": "p", "comment": ""}),
    ("post", "/tasks/t_nope/run", None),
    ("post", "/tasks/t_nope/steer", {"correction": "x"}),
    ("post", "/tasks/t_nope/pause", None),
    ("get", "/tasks/t_nope", None),
    ("get", "/tasks/t_nope/plan", None),
    ("get", "/tasks/t_nope/stream", None),
    ("get", "/tasks/t_nope/files", None),
])
def test_D75_an_unknown_task_is_a_404_on_every_task_route(ctx, method, path, body):
    c = _client(ctx, raise_server_exceptions=False)
    r = getattr(c, method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == 404, (path, r.status_code, r.text[:200])


def test_D77_a_path_the_filesystem_cannot_create_is_a_400(ctx, workspace):
    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=60))
    c = _client(ctx, raise_server_exceptions=False)
    long_name = "work/" + "a" * 300 + ".txt"                   # past any file name limit
    r = c.post(f"/tasks/{t.id}/files", json={"path": long_name,
                                              "content_b64": base64.b64encode(b"x").decode()})
    assert r.status_code == 400, (r.status_code, r.text[:200])


# AUDIT-ITERATION-3B, M1: D70's os.stat raised ValueError (not OSError) on an
# over-long path on Windows, so the gate crashed instead of deciding, and the
# browser and the drop route answered 500. A path the filesystem cannot hold
# is a 400 at the routes and an ordinary decision at the gate.
LONG = "work/" + "a/" * 20000                       # ~40 KB, past Windows' 32,767


def test_3B_M1_the_gate_decides_on_an_overlong_path(ctx, task, workspace):
    from agent.security.capabilities import gate_tool_call, is_protected

    assert isinstance(is_protected(LONG, str(ctx.config.root)), bool)
    for tool, args in (("read_file", {"path": LONG}),
                       ("write_file", {"path": LONG, "content": "x"})):
        d = gate_tool_call(ctx, task, ctx.tools.get(tool), args)
        assert d.verdict in ("allow", "deny", "needs_approval"), d


def test_3B_M1_the_routes_answer_400_for_an_overlong_path(ctx, workspace):
    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=60))
    c = _client(ctx, raise_server_exceptions=False)
    r = c.post(f"/tasks/{t.id}/files", json={"path": LONG,
                                              "content_b64": base64.b64encode(b"x").decode()})
    assert r.status_code == 400, (r.status_code, r.text[:200])
    # The browse route takes the path in the query string, which the client
    # library refuses at this length; the function is what the route calls.
    from agent.control import files

    with pytest.raises(files.Missing):        # nothing can exist at that path: a 404
        files.browse(ctx, ctx_task(ctx, t.id), LONG)


# AUDIT-ITERATION-3B, L3: D75 swept /tasks/{id}/* and missed the routes that
# take a task id elsewhere.
@pytest.mark.parametrize("method,path", [
    ("get", "/metrics/t_nope"),
    ("get", "/reviews/t_nope"),
    ("delete", "/suites/s_nope/tasks/t_nope"),
    ("delete", "/suites/s_nope"),
])
def test_3B_L3_an_unknown_id_is_a_404_outside_tasks_too(ctx, method, path):
    r = getattr(_client(ctx, raise_server_exceptions=False), method)(path)
    assert r.status_code == 404, (path, r.status_code, r.text[:200])


def test_3B_L3_deleting_what_exists_still_works(ctx):
    c = _client(ctx)
    sid = c.post("/suites", json={"name": "s", "description": ""}).json()
    sid = sid.get("suite_id") or sid.get("id")
    r = c.delete(f"/suites/{sid}")
    assert r.status_code == 200, r.text


def ctx_task(ctx, task_id):
    from agent.kernel.tasks import load_task

    return load_task(ctx.conn, task_id)
