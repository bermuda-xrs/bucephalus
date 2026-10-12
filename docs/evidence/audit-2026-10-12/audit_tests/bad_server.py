import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {}}) + "\n")
    elif m.get("method") == "tools/list":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": ["not", "a", "dict"]}) + "\n")
    sys.stdout.flush()
