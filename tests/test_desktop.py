"""The desktop shell's rules (agent/desktop.py): loopback only, state in
platformdirs, and closing the window stops every process it started.

No window is opened here: `main()` is start_server -> webview -> shutdown,
and the two halves that carry the rules are tested directly. The frozen
build's end-to-end close is in docs/evidence (step 3).
"""

from __future__ import annotations

import json
import os
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
    """Launched from the project directory, `state_root()` would pick the
    checkout - the developer's database. The app must not."""
    from pathlib import Path

    import agent.paths as paths

    checkout = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(checkout)
    # set-then-delete, so teardown restores it: resolve_state() writes the
    # variable, and a bare delenv of an unset one records nothing to undo.
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
    """A sandboxed run mid-flight, its grandchild, and a plain child: all gone
    after shutdown, and the server stopped. The orphan is the grandchild -
    the process a bare terminate of the child would leave behind."""
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
    """The shell must not open a hole the guard closes elsewhere."""
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
    """--windowed gives sys.stdout = sys.stderr = None, and uvicorn writes to
    them: the first frozen build died before its window opened."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    desktop._streams_to_log(tmp_path)
    # Without the fix this raises: "Unable to configure formatter 'default'".
    server, thread, port = desktop.start_server(ctx, "127.0.0.1")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
            assert r.status == 200
    finally:
        desktop.shutdown(server, thread)
        if sys.stdout:
            sys.stdout.close()
