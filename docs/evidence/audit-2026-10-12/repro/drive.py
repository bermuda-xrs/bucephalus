import sys, time, threading, types, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, sys.argv[2])
import agent.execute.mcp as M
from agent.execute.tools import ToolRegistry
ALL = []
class Rec(M.McpServer):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); ALL.append(self)
M.McpServer = Rec
mode = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
cfg = types.SimpleNamespace(argv=[sys.executable, os.path.join(here, "evil_mcp.py"), mode], env={}, read_only=[], timeout_s=2, start_timeout_s=2, strict_paths=False)
class Log:
    def append(self, *a): print("LOG", a[1], str(a[2])[:200])
ctx = types.SimpleNamespace(config=types.SimpleNamespace(mcp_servers={"evil": cfg}, root=here), tool_profile="full",
                            tools=ToolRegistry(), log=Log(), mcp_servers=[])
task = types.SimpleNamespace(id="t1")
def stop(sec, msg):
    def f():
        time.sleep(sec); print(msg(), flush=True)
        for s in ALL: s.close()
        os._exit(0)
    threading.Thread(target=f, daemon=True).start()
if mode == "paginate": stop(8, lambda: f"still inside attach after 8s (start_timeout_s=2); requests sent: {ALL[0]._next_id}")
if mode == "flood": stop(10, lambda: f"STILL BLOCKED in attach after 10s although start_timeout_s=2; queue size {ALL[0]._lines.qsize()}")
t0 = time.time()
try:
    M.attach(ctx, task)
    print("attach returned after", round(time.time()-t0,1), "s")
except Exception as e:
    print("attach raised", type(e).__name__, e)
    print("servers tracked for close_all:", len(ctx.mcp_servers), "; server process still running:", ALL[0].running)
    M.close_all(ctx)
    time.sleep(1); print("after close_all, still running:", ALL[0].running, ALL[0].proc.poll())
    ALL[0].close()
if mode == "inject":
    print(ctx.tools.manual())
    print(ctx.tools.schemas())
