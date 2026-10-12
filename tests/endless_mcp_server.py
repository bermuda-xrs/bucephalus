import json
import sys

n = 0
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {}}) + "\n")
    elif m.get("method") == "tools/list":
        n += 1
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {
            "tools": [{"name": f"t{n}", "inputSchema": {"type": "object", "properties": {}}}], "nextCursor": str(n)}}) + "\n")
    sys.stdout.flush()
