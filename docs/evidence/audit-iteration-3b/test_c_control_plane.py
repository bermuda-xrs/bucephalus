"""D74, D75, D47, D77 - the control plane (control/guard.py, server.py, files.py)."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control.guard import SECURITY_HEADERS
from agent.control.server import create_app
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract


def _client(ctx, workspace, scope=("work",)):
    task = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=list(scope)),
                       Budget(usd=1.0, seconds=60))
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c, task, app


def _drop(c, tid, path, data=b"dropped"):
    return c.post(f"/tasks/{tid}/files", json={"path": path, "content_b64": base64.b64encode(data).decode()})


def _missing_headers(r):
    return [k for k in SECURITY_HEADERS if k.lower() not in {h.lower() for h in r.headers}]


@pytest.mark.parametrize("raw", ["work/" + "a" * 300 + ".txt", "work/" + "b/" * 20000 + "c.txt",
                                 "work/" + "d/" * 200 + "e.txt"],
                         ids=["component-over-255", "path-40k", "path-600"])
def test_drop_of_an_overlong_path(ctx, workspace, raw, caplog):
    """D77 says an unwritable drop path is a 400."""
    c, task, _ = _client(ctx, workspace)
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        r = _drop(c, task.id, raw)
    exc = [rec.exc_info[0].__name__ for rec in caplog.records if rec.exc_info]
    print(f"\n{len(raw)} chars -> {r.status_code} {r.text[:80]!r} logged={exc} missing headers={_missing_headers(r)}")
    pytest.overlong_status = getattr(pytest, "overlong_status", {})
    pytest.overlong_status[len(raw)] = (r.status_code, exc)


def test_FINDING_a_40k_drop_path_is_still_a_500(ctx, workspace, caplog):
    c, task, _ = _client(ctx, workspace)
    raw = "work/" + "b/" * 20000 + "c.txt"
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        r = _drop(c, task.id, raw)
    exc = [rec.exc_info[1] for rec in caplog.records if rec.exc_info]
    print(f"\nstatus {r.status_code}; exception: {exc[0]!r}"[:300] if exc else f"\nstatus {r.status_code}")
    assert r.status_code == 500
    assert _missing_headers(r) == [], "D74 does hold: the 500 carries the headers"


def test_FINDING_browse_raises_valueerror_on_a_40k_path(ctx, workspace):
    """The GET route cannot be driven with a 40 KB query through the test client
    (httpx refuses the URL), so files.browse is called directly: it raises the
    same ValueError out of _allowed -> is_protected -> _protected_elsewhere, which
    get_files does not map (it catches Refused and Missing only)."""
    from agent.control import files
    c, task, _ = _client(ctx, workspace)
    with pytest.raises(ValueError, match="too long"):
        files.browse(ctx, task, "work/" + "b/" * 20000 + "c.txt")


def test_clean_a_route_exception_carries_every_header(ctx, workspace, monkeypatch):
    c, task, app = _client(ctx, workspace)
    import agent.control.server as srv
    monkeypatch.setattr(srv, "load_plan", lambda *a, **k: 1 / 0)
    r = c.get(f"/tasks/{task.id}")
    assert r.status_code == 500 and _missing_headers(r) == [] and "ZeroDivision" not in r.text


@pytest.mark.parametrize("tok", ["café", "ÿ" * 3, "x" * 10000, "Bearer", " "])
def test_clean_odd_tokens_are_401_not_500(ctx, workspace, tok):
    """D47. Query tokens arrive percent-decoded as UTF-8 str; header tokens as
    latin-1 str - both routes of the comparison are exercised."""
    c, task, app = _client(ctx, workspace)
    del c.headers["Authorization"]
    r = c.get("/tasks", params={"token": tok})
    codes = [r.status_code]
    for enc in ("utf-8", "latin-1"):
        codes.append(c.get("/tasks", headers={"x-alpha-token": tok.encode(enc)}).status_code)
        codes.append(c.get("/tasks", headers={"authorization": ("Bearer " + tok).encode(enc)}).status_code)
    assert codes == [401] * 5, codes


ROUTES = [("get", "/tasks/nope"), ("get", "/tasks/nope/stream"), ("post", "/tasks/nope/plan"),
          ("get", "/tasks/nope/plan"), ("post", "/tasks/nope/run"), ("post", "/tasks/nope/pause"),
          ("post", "/tasks/nope/steer"), ("post", "/tasks/nope/plan/approve"),
          ("post", "/tasks/nope/plan/reject"), ("get", "/tasks/nope/files"), ("post", "/tasks/nope/files")]


def test_every_task_route_with_an_unknown_id(ctx, workspace):
    c, task, app = _client(ctx, workspace)
    bodies = {"/tasks/nope/steer": {"correction": "x"}, "/tasks/nope/plan/approve": {"plan_id": "p"},
              "/tasks/nope/plan/reject": {"plan_id": "p", "comment": "c"},
              "/tasks/nope/files": {"path": "work/x", "content_b64": ""}}
    out = {}
    for m, path in ROUTES:
        r = getattr(c, m)(path, json=bodies.get(path)) if m == "post" else c.get(path)
        out[f"{m.upper()} {path}"] = r.status_code
    task_routes = sorted({r.path for r in app.routes if "{task_id}" in getattr(r, "path", "")})
    print("\n", out, "\nall task routes:", task_routes)
    assert all(v == 404 for v in out.values()), out


def test_note_the_task_id_routes_outside_tasks(ctx, workspace):
    """D75 says 'every task route 404s an unknown id'. Three routes take a
    {task_id} outside /tasks/ and do not go through _task."""
    c, task, app = _client(ctx, workspace)
    out = {"GET /metrics/nope": c.get("/metrics/nope").status_code,
           "GET /reviews/nope": c.get("/reviews/nope").status_code,
           "DELETE /suites/nope/tasks/nope": c.delete("/suites/nope/tasks/nope").status_code}
    print("\n", out)
