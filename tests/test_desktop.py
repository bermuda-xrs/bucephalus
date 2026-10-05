from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request

from agent import desktop
from agent.proc import _alive  # noqa: PLC2701


def test_loopback_unless_remote_is_opted_in(monkeypatch):
    monkeypatch.delenv("ALPHA_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv(desktop.HOST_ENV, "0.0.0.0")
    assert desktop.resolve_host() == "127.0.0.1", "a host env alone must not widen the bind"
    monkeypatch.setenv("ALPHA_ALLOW_REMOTE", "1")
    assert desktop.resolve_host() == "0.0.0.0"


def test_state_goes_to_platformdirs_even_from_inside_a_checkout(tmp_path, monkeypatch):
    from pathlib import Path

    import agent.paths as paths

    checkout = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("ALPHA_DATA_DIR", "placeholder")
    monkeypatch.delenv("ALPHA_DATA_DIR")
    monkeypatch.setenv("ALPHA_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path / "data")
    cfg, data = desktop.resolve_state()
    assert data == tmp_path / "data"
    assert paths.state_root(cfg) == tmp_path / "data", "state resolved to the checkout"
    text = cfg.read_text(encoding="utf-8")
    assert "x-expected" not in text and "fixtures" not in text, "task content in the config"


def _pythons_below() -> list[int]:
    return desktop.descendants()


def test_closing_the_window_kills_everything_it_started(ctx, workspace):
    from agent.execute.sandbox import run_sandboxed

    server, thread, port = desktop.start_server(ctx, "127.0.0.1")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
        assert r.status == 200
    code = ("import subprocess, sys, time\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "print(g.pid, flush=True)\ntime.sleep(120)\n")
    runner = threading.Thread(target=run_sandboxed, args=(code,),
                              kwargs={"timeout_s": 120}, daemon=True)
    runner.start()
    plain = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                             stdin=subprocess.DEVNULL)
    deadline = time.time() + 30
    while time.time() < deadline and len(_pythons_below()) < 3:
        time.sleep(0.2)
    started = _pythons_below()
    assert len(started) >= 3, f"the tree never formed: {started}"

    desktop.shutdown(server, thread)
    time.sleep(1)
    assert not thread.is_alive(), "the server kept running"
    alive = [p for p in started if _alive(p)]
    assert not alive, f"still running after the window closed: {alive}"
    plain.wait(timeout=5)


def test_the_console_answers_only_with_the_token(ctx, workspace):
    server, thread, port = desktop.start_server(ctx, "127.0.0.1")
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/approvals")
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("an unauthenticated call was served")
        except urllib.error.HTTPError as e:
            assert e.code == 401, e.code
        req.add_header("Authorization", "Bearer " + server.config.app.state.token)
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200 and isinstance(json.loads(r.read()), (list, dict))
    finally:
        desktop.shutdown(server, thread)


def test_a_windowed_build_without_streams_still_starts(tmp_path, monkeypatch, ctx, workspace):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    desktop._streams_to_log(tmp_path)
    server, thread, port = desktop.start_server(ctx, "127.0.0.1")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
            assert r.status == 200
    finally:
        desktop.shutdown(server, thread)
        if sys.stdout:
            sys.stdout.close()


def test_the_tree_stops_when_the_window_closes_not_after_its_teardown(monkeypatch, tmp_path):
    import types

    order = []

    class Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, fn):
            self.handlers.append(fn)
            return self

        def set(self):
            values = set()
            for fn in self.handlers:
                values.add(fn())

    window = types.SimpleNamespace(events=types.SimpleNamespace(closed=Event()))

    def start(**_kw):
        window.events.closed.set()
        order.append("webview teardown")

    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(
        create_window=lambda *a, **k: window, start=start))
    app = types.SimpleNamespace(state=types.SimpleNamespace(token="t"))
    monkeypatch.setattr(desktop, "start_server", lambda ctx, host: (
        types.SimpleNamespace(config=types.SimpleNamespace(app=app)), None, 1))
    monkeypatch.setattr(desktop, "shutdown", lambda *a, **k: order.append("tree stopped") or [1])
    monkeypatch.setenv("ALPHA_DATA_DIR", str(tmp_path / "data"))
    assert desktop.main([]) == 0
    assert order[:2] == ["tree stopped", "webview teardown"], order


def test_only_a_private_profile_is_deleted(monkeypatch, tmp_path):
    import types

    for private, kept in ((True, False), (False, True)):
        prof = tmp_path / f"profile-{private}"
        prof.mkdir()
        monkeypatch.setitem(sys.modules, "webview",
                            types.SimpleNamespace(_state={"private_mode": private}))
        monkeypatch.setitem(sys.modules, "webview.platforms.winforms",
                            types.SimpleNamespace(cache_dir=str(prof)))
        desktop._drop_private_profile()
        assert prof.exists() is kept, private


def test_the_final_stop_waits_for_the_early_one(monkeypatch):
    import types

    events, calls = [], []
    monkeypatch.setattr(desktop, "descendants",
                        lambda: calls.append(1) or ([4242] if len(calls) == 1 else []))

    def slow_kill(pid, **_kw):
        time.sleep(0.3)
        events.append(f"killed {pid}")
        return True
    monkeypatch.setattr("agent.proc.kill_tree", slow_kill)
    monkeypatch.setattr("agent.proc.process_start_time", lambda pid: 1.0)
    monkeypatch.setattr("agent.proc._alive", lambda pid: True)
    server = types.SimpleNamespace(should_exit=False)
    thread = types.SimpleNamespace(join=lambda *_a: None)
    early = threading.Thread(target=desktop.shutdown, args=(server, thread))
    early.start()
    while not calls:
        time.sleep(0.01)
    desktop.shutdown(server, thread)
    events.append("final stop returned")
    early.join()
    assert events == ["killed 4242", "final stop returned"], events


def test_an_open_console_stream_does_not_hold_the_close(ctx, workspace):
    import socket

    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=60))
    server, thread, port = desktop.start_server(ctx, "127.0.0.1")
    token = server.config.app.state.token
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(f"GET /tasks/{t.id}/stream HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
              f"Authorization: Bearer {token}\r\n\r\n".encode())
    assert s.recv(64).startswith(b"HTTP/1.1 200"), "the stream never opened"
    try:
        t0 = time.time()
        desktop.shutdown(server, thread)
        took = time.time() - t0
    finally:
        s.close()
    assert not thread.is_alive(), "the server kept running"
    assert took < 3, f"an open stream held the stop for {took:.1f} s"


def _unbounded_servers(source: str) -> list[int]:
    import ast

    return [n.lineno for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and getattr(n.func.value, "id", "") == "uvicorn" and n.func.attr in ("run", "Config")
            and not any(k.arg == "timeout_graceful_shutdown" for k in n.keywords)]


def test_every_server_bounds_its_shutdown():
    from pathlib import Path

    src = Path(desktop.__file__).resolve().parent
    found = {str(p.relative_to(src)): _unbounded_servers(p.read_text(encoding="utf-8"))
             for p in src.rglob("*.py")}
    assert not {k: v for k, v in found.items() if v}, found
