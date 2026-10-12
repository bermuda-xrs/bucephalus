import sys, time, types, os
sys.path.insert(0, sys.argv[1])
import agent.execute.mcp as M
here = os.path.dirname(os.path.abspath(__file__))
cfg = types.SimpleNamespace(argv=[sys.executable, os.path.join(here, "evil_mcp.py"), "inject"], env={}, read_only=[], timeout_s=2, start_timeout_s=2)
s = M.McpServer("e", cfg, here); s.start()
t=time.time(); s.close(); print("close took", round(time.time()-t,2), "running:", s.running, "poll", s.proc.poll())
time.sleep(1); print("after 1s running:", s.running)
