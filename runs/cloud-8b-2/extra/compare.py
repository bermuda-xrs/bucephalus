"""Side-by-side task x arm x rep table: first run (cloud-8b) vs re-run (cloud-8b-2), from the two paired.json files."""
import json
import sys

old = json.load(open(sys.argv[1], encoding="utf-8"))
new = json.load(open(sys.argv[2], encoding="utf-8"))


def table(p):
    return {(r["key"], a, r["rep"]): ("P" if r["passed"][a] else "F", r["status"][a])
            for r in p["pairs"] for a in r["passed"]}


o, n = table(old), table(new)
arms = ["control", "full8b", "small8b"]
keys = sorted({k for k, _, _ in o} | {k for k, _, _ in n})
print(f"| task | arm | {' | '.join(f'rep {r} (1st -> 2nd)' for r in range(3))} |")
print("|---|---|" + "---|" * 3)
for k in keys:
    for a in arms:
        cells = []
        for rep in range(3):
            x, y = o.get((k, a, rep), ("-", "-")), n.get((k, a, rep), ("-", "-"))
            mark = "" if x[0] == y[0] else " **moved**"
            cells.append(f"{x[0]} {x[1]} -> {y[0]} {y[1]}{mark}")
        print(f"| {k} | {a} | {' | '.join(cells)} |")
print()
for a in arms:
    print(a, "1st:", old["arms"].get(a, {}).get("passes"), "2nd:", new["arms"].get(a, {}).get("passes"),
          "| mean_wall_s 1st:", old["arms"].get(a, {}).get("mean_wall_s"), "2nd:", new["arms"].get(a, {}).get("mean_wall_s"))
for a, v in new.get("vs_control", {}).items():
    print("2nd", a, "vs control:", v)
