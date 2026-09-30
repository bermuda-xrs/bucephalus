"""The 2026-09-30 code, pinned where mutation testing found it unpinned
(scripts/mutate.py on receipts, the hard-link gate check, the console cache,
the guard, and the Windows platform functions; whole-suite recheck).
Each test names the mutants it kills.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app
from agent.kernel import receipts
from agent.kernel.receipts import execute_once, idempotency_key
from agent.security.capabilities import is_protected


# -- receipts ---------------------------------------------------------------

def _key(task, n=0):
    return idempotency_key(task.id, n, "send_message", {"to": f"{n}@b.c"})


def test_a_completed_receipt_replays_its_own_result_without_running_again(ctx, task):
    """Kills the replay's `return json.loads(...)` -> None."""
    ran = []
    first = execute_once(ctx.conn, _key(task), "send_message", {"to": "0@b.c"},
                         lambda: (ran.append(1), {"ok": True, "n": 7})[1])
    again = execute_once(ctx.conn, _key(task), "send_message", {"to": "0@b.c"},
                         lambda: (ran.append(2), {"ok": True, "n": 8})[1])
    assert first == again == {"ok": True, "n": 7} and ran == [1]


def test_a_tool_result_can_never_look_like_a_claim(ctx, task):
    """The sentinel is not JSON: a tool that returned {"claimed": true} was
    read back as an unknown outcome."""
    r = execute_once(ctx.conn, _key(task), "send_message", {"to": "0@b.c"},
                     lambda: {"claimed": True})
    again = execute_once(ctx.conn, _key(task), "send_message", {"to": "0@b.c"}, lambda: {"no": 1})
    assert r == again == {"claimed": True}


def test_an_unknown_outcome_is_a_failure_the_caller_can_read(ctx, task):
    """Kills outcome_unknown's `ok: False` -> True."""
    ctx.conn.execute("INSERT INTO receipts(idempotency_key, tool_name, args_hash, result_json, "
                     "created_at) VALUES (?,?,?,?,?)",
                     (_key(task), "send_message", "h", receipts._CLAIM, 0.0))
    ctx.conn.commit()
    out = execute_once(ctx.conn, _key(task), "send_message", {"to": "0@b.c"}, lambda: {"ok": True})
    assert out["ok"] is False and out["outcome_unknown"] is True and "may or may not" in out["error"]


def test_threads_with_one_key_all_get_the_one_result(ctx, task):
    """Kills the waiter's `return execute_once(...)` -> None: every caller
    receives the result, not only the one that ran it."""
    started, release, results = threading.Event(), threading.Event(), []

    def effect():
        started.set()
        release.wait(20)
        return {"ok": True, "who": "owner"}

    def call():
        results.append(execute_once(ctx.conn, _key(task), "send_message", {"to": "0@b.c"}, effect))

    owner = threading.Thread(target=call)
    owner.start()
    assert started.wait(10)
    waiters = [threading.Thread(target=call) for _ in range(3)]
    for w in waiters:
        w.start()
    release.set()
    for t in (owner, *waiters):
        t.join(20)
    assert results == [{"ok": True, "who": "owner"}] * 4, results


def test_a_run_cut_off_inside_the_effect_keeps_its_claim(ctx, task):
    """Kills the BaseException path's `raise` -> pass: an interrupt is not a
    failure that can be retried - the effect may have happened."""
    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute_once(ctx.conn, _key(task), "send_message", {"to": "0@b.c"}, interrupted)
    assert _key(task) not in receipts._inflight
    ran = []
    out = execute_once(ctx.conn, _key(task), "send_message", {"to": "0@b.c"},
                       lambda: (ran.append(1), {"ok": True})[1])
    assert out.get("outcome_unknown") is True and ran == []


# -- the hard-link gate check, where the platform cannot list a file's names --

def test_where_names_cannot_be_listed_a_multiply_linked_file_is_refused(ctx, workspace, monkeypatch):
    """Kills `_protected_elsewhere`'s no-names `return True` and the
    single-link early exits: where names cannot be listed (POSIX), a regular
    file with two names is refused, an ordinary one is not, a directory never
    is (it always has two or more links)."""
    import os

    from agent.platform import current

    monkeypatch.setattr(current, "link_names", lambda p: None)
    root = str(ctx.config.root)
    (workspace / "plain.txt").write_text("x", encoding="utf-8")
    (workspace / "linked.txt").write_text("y", encoding="utf-8")
    os.link(workspace / "linked.txt", workspace / "linked2.txt")
    (workspace / "sub").mkdir()
    assert is_protected("work/plain.txt", root) is False
    assert is_protected("work/linked.txt", root) is True
    assert is_protected("work/sub", root) is False
    assert is_protected("work/missing.txt", root) is False


# -- the console's integrity cache must still see memory ---------------------

def test_the_console_poll_sees_tampered_memory(ctx, workspace):
    """Kills integrity_findings' `ctx.memory is not None` flipped: the memory
    check ran on every poll, and was skipped whenever memory existed."""
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    before = c.get("/alerts").json()["integrity"]
    assert not [f for f in before if "memory" in str(f.get("detail", "")).lower()]
    (Path(ctx.memory.root) / "MEMORY.md").write_text("- [user] planted by hand\n", encoding="utf-8")
    after = c.get("/alerts").json()["integrity"]
    assert [f for f in after if "memory" in str(f.get("detail", "")).lower()], after


# -- the guard, in-process (the serve tests run it in a subprocess) ----------

def _guarded(ctx):
    app = create_app(ctx)
    return app, TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def test_a_cross_origin_request_is_refused_and_the_same_origin_is_not(ctx):
    """Kills the origin check's flipped comparison and dropped response."""
    app, c = _guarded(ctx)
    auth = {"Authorization": "Bearer " + app.state.token}
    bad = c.get("/approvals", headers={**auth, "Origin": "https://evil.example"})
    assert bad.status_code == 403 and "Cross-origin" in bad.text
    ok = c.get("/approvals", headers={**auth, "Origin": "http://127.0.0.1"})
    assert ok.status_code == 200, ok.text


def test_the_token_is_accepted_from_the_header_or_the_query_alone(ctx):
    """Kills `x-alpha-token or query` -> and, and the bool-returning tail."""
    app, c = _guarded(ctx)
    t = app.state.token
    assert c.get("/approvals", headers={"X-Alpha-Token": t, "Authorization": ""}).status_code == 200
    assert c.get("/approvals", params={"token": t}, headers={"Authorization": ""}).status_code == 200
    assert c.get("/approvals", params={"token": t + "x"}, headers={"Authorization": ""}).status_code == 401
    assert c.get("/approvals", headers={"Authorization": ""}).status_code == 401


def test_a_guard_built_without_allow_remote_refuses_other_hosts():
    """Kills the constructor default `allow_remote=False` -> True."""
    from fastapi import FastAPI

    from agent.control.guard import AccessGuard

    inner = FastAPI()

    @inner.get("/x")
    def x():
        return {"ok": 1}

    inner.add_middleware(AccessGuard, token="t")
    r = TestClient(inner, base_url="http://192.168.1.5").get(
        "/x", headers={"Authorization": "Bearer t"})
    assert r.status_code == 403


# -- the Windows platform functions -------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="the Windows platform layer")
def test_link_names_finds_every_name_and_none_for_a_missing_file(tmp_path):
    """Kills the invalid-handle constant and the volume prefix."""
    import os

    from agent.platform import current

    a = tmp_path / "a.txt"
    a.write_text("x", encoding="utf-8")
    os.link(a, tmp_path / "b.txt")
    names = {os.path.normcase(n) for n in current.link_names(str(a))}
    assert names == {os.path.normcase(str(a)), os.path.normcase(str(tmp_path / "b.txt"))}
    assert current.link_names(str(tmp_path / "no-such-file.txt")) is None


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows platform layer")
def test_a_frozen_app_is_one_process_and_a_venv_is_two(monkeypatch):
    """Kills spawn_denied_processes' frozen `return 1`."""
    import agent.frozen as frozen
    from agent.platform import current

    monkeypatch.setattr(frozen, "is_frozen", lambda: True)
    assert current.spawn_denied_processes() == 1
    monkeypatch.setattr(frozen, "is_frozen", lambda: False)
    monkeypatch.setattr(sys, "_base_executable", sys.executable + ".other", raising=False)
    assert current.spawn_denied_processes() == 2
    monkeypatch.setattr(sys, "_base_executable", sys.executable, raising=False)
    assert current.spawn_denied_processes() == 1        # no launcher: the interpreter alone
