"""Step 3 checks on a profile_run output folder. Reads every agent.local.db read-only; runs nothing from data/."""
import glob
import json
import os
import re
import sqlite3
import sys

out = sys.argv[1]
expect_sha = sys.argv[2]
PATH_RE = re.compile(r'"path":\s*"((?:[^"\\]|\\.)*)"')
GOALS = {"negative-control-004": "Count how many .json files", "handbook-recall-005": "work/handbook.md"}

report = {"artifact_paths": {}, "neg_control_full8b": {}, "denied": {}}
for db in sorted(glob.glob(os.path.join(out, "data", "*-[0-9]", "agent.local.db"))):
    run = db.split(os.sep)[-2]
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    paths = [p for (content,) in c.execute("select content from artifacts where kind='tool_output'")
             for p in PATH_RE.findall(content or "")]
    rel = [p for p in paths if not os.path.isabs(p)]
    report["artifact_paths"][run] = {"n_paths": len(paths), "relative": len(rel),
                                     "start_runs/": sum(p.startswith("runs/") for p in paths),
                                     "examples_relative": sorted(set(rel))[:5]}
    dots, inside_work, other = 0, [], []
    for task_id, seq, payload in c.execute(
            "select task_id, seq, payload_json from events where type='human.denied' order by task_id, seq"):
        reason = json.loads(payload).get("reason", "")
        m = re.match(r"path='([^']*)'", reason)
        p = m.group(1) if m else None
        if p in (".", "./"):
            dots += 1
        elif p is not None and "protected path" in reason and (p == "work" or p.startswith("work/") or "/work/" in p):
            inside_work.append({"task_id": task_id, "seq": seq, "reason": reason[:160]})
        else:
            other.append({"task_id": task_id, "seq": seq, "reason": reason[:160]})
    report["denied"][run] = {"dot_refusals": dots, "inside_work_refusals": inside_work, "other": other}
    row = c.execute("select id from tasks where contract_json like ?",
                    (f"%{GOALS['negative-control-004']}%",)).fetchone()
    if run.startswith("full8b-") and row is None:
        report["neg_control_full8b"][run] = "task not created (yet)"
    elif run.startswith("full8b-"):
        (tid,) = row
        intents = [(seq, json.loads(p)) for seq, p in c.execute(
            "select seq, payload_json from events where task_id=? and type='tool.intent' order by seq", (tid,))]
        searches = [(s, p["preview"]) for s, p in intents if p.get("tool") in ("glob", "grep", "search", "list_dir")]
        outs = [x for (x,) in c.execute("select content from artifacts where task_id=? and kind='tool_output'", (tid,))]
        report["neg_control_full8b"][run] = {
            "task_id": tid, "tool_calls": [(s, p.get("preview")) for s, p in intents],
            "searched_work": bool(searches),
            "an_output_lists_a.json_and_b.json": any("a.json" in o and "b.json" in o for o in outs)}
    c.close()

paired = json.load(open(os.path.join(out, "paired.json"), encoding="utf-8"))
report["paired_code"] = {"git_commit": paired["code"].get("git_commit"), "git_dirty": paired["code"].get("git_dirty"),
                         "commit_matches": paired["code"].get("git_commit") == expect_sha}
print(json.dumps(report, indent=1))
