"""Print one task's trajectory from an agent.local.db, read-only: tool calls, refusals, verify results, answer.
Usage: trajectory.py <db> <goal substring>. Output is untrusted model text, shown as data."""
import json
import sqlite3
import sys

db, goal = sys.argv[1], sys.argv[2]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
tid, status = c.execute("select id, status from tasks where contract_json like ?", (f"%{goal}%",)).fetchone()
print(f"db={db.split('/')[-2]} task_id={tid} final_status={status}")
SHOW = {"tool.intent", "tool.receipt", "human.denied", "verify.failed", "verify.passed", "task.status_changed",
        "task.crashed", "stuck.detected", "tool.not_exposed", "hook.denied"}
for seq, typ, payload in c.execute("select seq, type, payload_json from events where task_id=? order by seq", (tid,)):
    if typ in SHOW or "stuck" in typ or "plateau" in typ:
        p = json.loads(payload)
        if typ == "tool.intent":
            s = p.get("preview", "")
        elif typ == "tool.receipt":
            s = f"ok={p.get('ok')} tool={p.get('tool')}"
        else:
            s = json.dumps(p)[:260]
        print(f"  seq {seq:3d} {typ:20s} {s[:260]}")
for kind, content in c.execute("select kind, content from artifacts where task_id=? and kind='answer' order by created_at", (tid,)):
    print(f"  ANSWER: {content[:400]!r}")
