"""Audit probes: guard.SECURITY_HEADERS on every response, and auth on every
route iteration 3 added. Scratch state only (conftest.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.control.guard import SECURITY_HEADERS
from agent.control.server import create_app

NEW_ROUTES = [
    ("GET", "/conversations"), ("POST", "/conversations"), ("PATCH", "/conversations/c_x"),
    ("GET", "/conversations/c_x"), ("POST", "/conversations/c_x/messages"),
    ("GET", "/context"), ("GET", "/memory"), ("POST", "/memory/accept"),
    ("GET", "/panels/schedules"), ("GET", "/panels/channels"), ("GET", "/panels/integrity"),
    ("GET", "/panels/measurement"), ("GET", "/panels/settings"), ("GET", "/panels/logs"),
    ("GET", "/tasks/x/files"), ("POST", "/tasks/x/files"),
]


@pytest.fixture
def app(ctx, workspace):
    return create_app(ctx)


def _missing(r):
    return sorted(k for k in SECURITY_HEADERS if r.headers.get(k) != SECURITY_HEADERS[k])


@pytest.mark.parametrize("method,path", NEW_ROUTES)
def test_clean_every_new_route_refuses_without_the_token_and_carries_the_headers(app, method, path):
    c = TestClient(app, base_url="http://127.0.0.1")
    r = c.request(method, path, json={})
    assert r.status_code == 401 and not _missing(r)


@pytest.mark.parametrize("method,path", NEW_ROUTES)
def test_clean_every_new_route_refuses_a_foreign_origin_even_with_the_token(app, method, path):
    c = TestClient(app, base_url="http://127.0.0.1")
    r = c.request(method, path, json={}, headers={"Authorization": "Bearer " + app.state.token,
                                                   "Origin": "http://evil.example"})
    assert r.status_code == 403 and not _missing(r)


def test_clean_rebinding_host_is_refused_with_the_headers(app):
    c = TestClient(app, base_url="http://evil.example")
    r = c.get("/", headers={"Host": "evil.example"})
    assert r.status_code == 403 and not _missing(r)


def test_clean_ordinary_errors_carry_the_headers(app):
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    for method, path, body, want in [("GET", "/nope", None, 404), ("DELETE", "/memory", None, 405),
                                     ("POST", "/conversations/x/messages", {"x": 1}, 422),
                                     ("GET", "/panels/nope", None, 404)]:
        r = c.request(method, path, json=body)
        assert r.status_code == want and not _missing(r), (path, r.status_code, _missing(r))


def test_FINDING_a_500_carries_none_of_the_security_headers(app):
    """BaseHTTPMiddleware adds the headers to the response call_next returns.
    An exception in a route, or in the guard itself, never returns one: the
    outer ServerErrorMiddleware writes a bare 500 with no CSP, no
    X-Frame-Options and no nosniff. The docstring says "EVERY response,
    refusals included"."""
    c = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    tok = {"Authorization": "Bearer " + app.state.token}
    route_crash = c.post("/tasks/no-such-task/plan", headers=tok)          # load_task KeyError
    guard_crash = c.get("/tasks?token=café")                          # AUDIT F9, still open
    for r in (route_crash, guard_crash):
        print(r.status_code, _missing(r))
        assert r.status_code == 500
        assert _missing(r) == sorted(SECURITY_HEADERS)


def test_FINDING_unknown_task_ids_are_500s_on_the_task_routes(app):
    """The new `_task` helper maps KeyError to 404, but only the two file
    routes use it; the plan and run routes the console calls still 500."""
    c = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    c.headers["Authorization"] = "Bearer " + app.state.token
    got = {p: c.post(p, json={"plan_id": "x", "comment": "c", "edits": []}).status_code
           for p in ("/tasks/nope/plan", "/tasks/nope/plan/approve", "/tasks/nope/plan/reject",
                     "/tasks/nope/run", "/tasks/nope/pause")}
    got["GET /tasks/nope/files"] = c.get("/tasks/nope/files").status_code
    print(got)
    assert got["GET /tasks/nope/files"] == 404
    assert got["/tasks/nope/plan"] == 500 and got["/tasks/nope/run"] == 500
