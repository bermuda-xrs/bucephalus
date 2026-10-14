import json
import os
import sys
import time

S = json.loads(os.environ.get("MCP_SCRIPT", "{}"))


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def ask(method, rid):
    send({"jsonrpc": "2.0", "id": rid, "method": method})
    reply = json.loads(sys.stdin.readline())
    sys.stderr.write(f"asked {method}: {json.dumps(reply)}\n")
    sys.stderr.flush()
    return reply


for line in sys.stdin:
    m = json.loads(line)
    method, rid = m.get("method"), m.get("id")
    if rid is None:
        continue
    if method in S.get("exit_on", []):
        sys.stderr.write("dying as scripted\n")
        sys.stderr.flush()
        sys.exit(5)
    if method in S.get("ask_first", {}):
        reply = ask(S["ask_first"][method], 700)
        if S.get("ask_wants") and S["ask_wants"] not in reply:
            sys.exit(6)
    if method in S.get("raw", {}):
        send({"jsonrpc": "2.0", "id": rid, **S["raw"][method]})
    elif method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method == "tools/list":
        time.sleep(S.get("list_delay_s", 0))
        pages = S.get("pages", 1)
        page = int((m.get("params") or {}).get("cursor", "0"))
        res = {"tools": S.get("tools", []) if page == 0 else []}
        if page + 1 < pages:
            res["nextCursor"] = str(page + 1)
        send({"jsonrpc": "2.0", "id": rid, "result": res})
    elif method == "tools/call" and S.get("echo_env"):
        send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": ",".join(sorted(os.environ))}]}})
    elif method == "tools/call":
        send({"jsonrpc": "2.0", "id": rid, "result": S.get("call", {"content": []})})
