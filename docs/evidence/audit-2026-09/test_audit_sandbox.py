"""AUDIT-2026-09 evidence: the sandbox's hook, in agent-code mode
(protected_paths non-empty => deny_spawn and the read fence are on).

Loopback only; nothing leaves the machine. Each test asserts the CURRENT
behaviour, so green is the demonstration.
"""
from __future__ import annotations

import socket
import threading

from agent.execute.sandbox import run_sandboxed

PROTECTED = ("C:/nonexistent-protected-marker",)


def test_F_sandbox_has_no_network_fence():
    """execute/libraries.py: 'The sandbox currently makes three promises: no
    network, ...'. The hook has no socket event; a loopback connect succeeds."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    got = []

    def accept():
        c, _ = srv.accept()
        got.append(c.recv(16))
        c.close()

    th = threading.Thread(target=accept, daemon=True)
    th.start()
    r = run_sandboxed(
        f"import socket; s=socket.create_connection(('127.0.0.1',{port}),5); "
        "s.sendall(b'hello'); s.close(); print('connected')",
        timeout_s=30, protected_paths=PROTECTED)
    th.join(10)
    srv.close()
    print(r)
    assert "connected" in r["stdout"]
    assert got == [b"hello"]


def test_F_spawn_denial_misses_winapi_createprocess():
    """The spawn list names subprocess.Popen, os.system, os.exec, os.spawn,
    os.posix_spawn, os.startfile - not _winapi.CreateProcess, which is what
    multiprocessing uses on Windows."""
    code = r'''
import subprocess, _winapi, os
try:
    subprocess.Popen(["cmd", "/c", "exit 0"])
    print("popen: allowed")
except PermissionError:
    print("popen: refused")
h = _winapi.CreateProcess(None, "cmd /c exit 7", None, None, False, 0, None, None, None)
_winapi.WaitForSingleObject(h[0], 10000)
print("createprocess exit", _winapi.GetExitCodeProcess(h[0]))
'''
    r = run_sandboxed(code, timeout_s=30, protected_paths=PROTECTED)
    print(r)
    assert "popen: refused" in r["stdout"]
    assert "createprocess exit 7" in r["stdout"]
