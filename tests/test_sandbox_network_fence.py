from __future__ import annotations

from agent.execute.sandbox import run_sandboxed

REACH = ("import socket\n"
         "try:\n"
         "    s = socket.create_connection(('127.0.0.1', {port}), timeout=5)\n"
         "    s.sendall(b'GET / HTTP/1.0\\r\\nHost: 127.0.0.1\\r\\n\\r\\n')\n"
         "    print('REACHED', s.recv(64))\n"
         "except Exception as e:\n"
         "    print('REFUSED', type(e).__name__, e)\n")


def test_sandboxed_code_cannot_reach_the_control_plane(ctx, workspace):
    from agent import desktop

    server, thread, port = desktop.start_server(ctx, "127.0.0.1")
    try:
        r = run_sandboxed(REACH.format(port=port), timeout_s=30)
    finally:
        desktop.shutdown(server, thread)
    assert "REACHED" not in r["stdout"], r
    assert "network access is denied" in r["stdout"], r


def test_no_socket_can_be_made_or_resolved(workspace):
    code = ("import socket\n"
            "for name, f in [('socket', lambda: socket.socket()),\n"
            "                ('resolve', lambda: socket.getaddrinfo('example.com', 80)),\n"
            "                ('udp', lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM))]:\n"
            "    try:\n"
            "        f(); print('ALLOWED', name)\n"
            "    except PermissionError:\n"
            "        print('REFUSED', name)\n")
    r = run_sandboxed(code, timeout_s=30)
    assert "ALLOWED" not in r["stdout"], r
    assert r["stdout"].count("REFUSED") == 3, r


def test_the_tool_bridge_still_works_without_sockets(ctx, task, workspace):
    from agent.execute.codeact import run_code_action

    (workspace / "hello.txt").write_text("contents here", encoding="utf-8")
    r = run_code_action(ctx, task, "r = call_tool('read_file', path='work/hello.txt')\n"
                                   "print('GOT:' + r['content'])\n", timeout_s=30)
    assert "GOT:contents here" in r["stdout"], r
