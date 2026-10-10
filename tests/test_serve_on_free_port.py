from __future__ import annotations

import subprocess
import sys
import urllib.request

import pytest

from conftest import serve_on_free_port
from agent.proc import kill_tree


def _probe(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2):  # noqa: S310
            return True
    except Exception:  # noqa: BLE001
        return False


def test_a_port_taken_between_choosing_and_binding_is_retried():
    tried = []

    def launch(port):
        tried.append(port)
        if len(tried) == 1:
            return subprocess.Popen([sys.executable, "-c", "raise SystemExit(1)"])
        return subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    p, port = serve_on_free_port(launch, _probe, timeout=30)
    try:
        assert len(tried) == 2 and port == tried[1] and _probe(port)
    finally:
        kill_tree(p.pid)
        p.wait(10)


def test_a_server_that_never_starts_fails_after_the_attempts():
    tried = []

    def launch(port):
        tried.append(port)
        return subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])

    with pytest.raises(RuntimeError, match="3 attempts"):
        serve_on_free_port(launch, _probe, attempts=3)
    assert len(tried) == 3


def test_a_server_that_runs_but_never_answers_is_killed():
    procs = []

    def launch(port):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        procs.append(p)
        return p

    with pytest.raises(TimeoutError):
        serve_on_free_port(launch, _probe, timeout=1.5)
    assert procs[0].wait(10) is not None
