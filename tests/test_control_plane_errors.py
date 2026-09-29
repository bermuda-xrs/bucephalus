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
