import json, os, pathlib, sys
from agent.paths import state_root, ensure_state_dirs
root = state_root(None)
print("state_root ->", root)
print("is the checkout? ", "alpha-test-1" in str(root) and "OneDrive" in str(root))
ensure_state_dirs(root)
# a real task, end to end, with a mock backend so no model is needed
from agent.config import Config, BackendConfig, Limits, AutonomyConfig, GateConfig
from agent.context import AgentContext
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.execute.loop import run_task
from agent.workspace import reset_workspace

cfg = Config(
    db_path=str(pathlib.Path(root)/"agent.db"),
    anchor_path=str(pathlib.Path(root)/"anchor"/"chain.anchor"),
    skills_path=str(pathlib.Path(root)/"skills_store"),
    root=str(root),
    backends={
      "cloud": BackendConfig(kind="mock", model="m", trust_tier="cloud", max_output_tokens=512),
      "review": BackendConfig(kind="mock", model="r", trust_tier="cloud", max_output_tokens=128),
    },
    limits=Limits(max_steps=3, default_task_budget_usd=1.0, default_task_budget_seconds=60),
    autonomy=AutonomyConfig(auto_review=False, approve_scoped_writes=True, plan_required=False),
    gate=GateConfig(),
)
ctx = AgentContext(cfg)
VERIFY = ("import json, pathlib\n"
          "def check():\n"
          "    p = pathlib.Path('output/answer.json')\n"
          "    if not p.exists(): return False, 0.0\n"
          "    return json.loads(p.read_text()).get('x')==7, 1.0\n")
fx = {"work/P1/output/.keep": "", "grading/P1/verify.py": VERIFY}
reset_workspace(root, ["work/P1"], fx)
code = ("import json,pathlib\n"
        "p=pathlib.Path('work/P1/output/answer.json')\n"
        "p.parent.mkdir(parents=True,exist_ok=True)\n"
        "p.write_text(json.dumps({'x':7}))\n")
ctx.backends["cloud"].queue(json.dumps({"kind":"code","code":code}),
                            '<final>{"x": 7}</final>')
t = create_task(ctx.conn, ctx.log, TaskContract(
    goal="write work/P1/output/answer.json with x=7",
    success_criterion="verify_program",
    artifact_scope=["work/P1"], grader_dir="grading/P1"), Budget(usd=1.0, seconds=60))
res = run_task(ctx, t.id)
print("RESULT:", res.status)
ok, bad = ctx.log.verify(t.id)
print("chain verifies:", ok)
print("db written under state_root:", (pathlib.Path(root)/"agent.db").exists())
print("nothing written to cwd:", not (pathlib.Path.cwd()/"agent.db").exists())
ctx.close()

# The console's HTML is package DATA, not code, so an installed wheel can pass
# every check above and still 500 on GET / - which is exactly what happened
# until 2026-09-06 (SPEC-DELTA C24). "An installed wheel can run a task" was
# never the same claim as "an installed wheel is usable".
import agent.control.server as _srv
_ui = pathlib.Path(_srv.__file__).parent / "ui.html"
print("console html shipped:", _ui.exists())

sys.exit(0 if res.status=="done" and ok and _ui.exists() else 1)
