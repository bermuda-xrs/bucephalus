"""AUDIT-2026-09: desktop.descendants() trusts ppid with no start-time check.

Read-only: lists live processes P whose recorded parent pid Q is alive but was
CREATED AFTER P - i.e. Q is a recycled pid, not P's parent. If Q were the
desktop app, shutdown() would kill_tree(P) (taskkill /T /F: P and its tree).
proc.orphan_pids() already rejects exactly this case by comparing start times.
Nothing is killed here.
"""
from agent.desktop import descendants
from agent.proc import process_start_time, process_table

rows = process_table()
live = {pid for pid, _, _ in rows}
names = {pid: img for pid, _, img in rows}
hits = []
for pid, ppid, img in rows:
    if ppid <= 0 or ppid not in live or pid == ppid:
        continue
    c, p = process_start_time(pid), process_start_time(ppid)
    if c is not None and p is not None and p > c:
        hits.append((ppid, pid, img))
print(f"{len(rows)} processes; {len(hits)} with a recycled 'parent' pid")
for ppid, pid, img in hits[:10]:
    d = descendants(ppid)
    print(f"  fake parent {ppid} ({names.get(ppid)}) -> real process {pid} ({img}); "
          f"descendants({ppid}) includes it: {pid in d}; would kill {len(d)} pid(s)")

# Exposure today: live processes whose recorded parent pid is DEAD. Each is
# adopted by descendants() of whichever process next receives that pid.
dangling = [(pid, ppid, img) for pid, ppid, img in rows if ppid > 0 and ppid not in live]
print(f"{len(dangling)} live processes name a dead parent pid, e.g.:",
      [(img, ppid) for _, ppid, img in dangling[:6]])

# The decision itself, on a table where our own pid was recycled from the dead
# parent of explorer.exe (start times: explorer 100, us 200).
import os
import agent.proc as P
me = os.getpid()
P._platform = type("F", (), {
    "process_table": staticmethod(lambda: [(me, 1, "alpha.exe"), (4242, me, "explorer.exe")]),
    "process_start_time": staticmethod(lambda pid: {me: 200, 4242: 100}.get(pid)),
})
print("fake table: descendants() =", descendants(), "-> shutdown() would kill_tree(4242)")
