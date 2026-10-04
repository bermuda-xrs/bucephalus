"""M5 acceptance — the action substrate, the sandbox boundary, and the bridge."""

from __future__ import annotations

import json
from pathlib import Path

from agent.execute.codeact import run_code_action
from agent.execute.sandbox import run_sandboxed


def test_sandbox_runs_and_reports():
    r = run_sandboxed("print('hello from the box')")
    assert r["returncode"] == 0
    assert "hello from the box" in r["stdout"]


def test_sandbox_timeout():
    r = run_sandboxed("while True: pass", timeout_s=3)
    assert r["stderr"] == "TIMEOUT"
    assert r["returncode"] == -1


def test_sandbox_denies_anchor_write(ctx, task, workspace):
    """Code attempting to write anchor_path fails, and the anchor is untouched."""
    anchor_path = Path(ctx.config.resolve(ctx.config.anchor_path))
    ctx.anchor.record(task.id, "deadbeef")
    before = anchor_path.read_bytes()

    code = (
        "from pathlib import Path\n"
        f"p = Path(r'{anchor_path}')\n"
        "p.write_text('rewritten by the agent')\n"
        "print('WROTE')\n"
    )
    # The gate refuses before the sandbox ever sees it.
    from agent.security.capabilities import gate_tool_call

    tool = ctx.tools.get("write_file")
    decision = gate_tool_call(
        ctx, task, tool, {"path": str(anchor_path), "content": "x"}
    )
    assert decision.verdict == "deny"
    assert "protected path" in decision.reason or "artifact_scope" in decision.reason

    # And the audit hook inside the child refuses the direct write too.
    r = run_sandboxed(code, allowed_paths=[str(workspace)], timeout_s=15)
    assert r["returncode"] != 0
    assert "write denied outside scope" in r["stderr"]
    assert anchor_path.read_bytes() == before


def test_sandbox_allows_writes_inside_scope(workspace):
    code = (
        "from pathlib import Path\n"
        "Path('work/made.txt').write_text('inside')\n"
        "print('ok')\n"
    )
    r = run_sandboxed(code, allowed_paths=[str(workspace)], timeout_s=15)
    assert r["returncode"] == 0, r["stderr"]
    assert (workspace / "made.txt").read_text(encoding="utf-8") == "inside"


def test_sandbox_exposes_only_scoped_paths(workspace, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read me", encoding="utf-8")
    (workspace / "ok.txt").write_text("fine", encoding="utf-8")

    r = run_sandboxed(
        "import os\nprint(sorted(os.listdir('.')))\n",
        allowed_paths=[str(workspace)], timeout_s=15,
    )
    assert "secret.txt" not in r["stdout"]


def test_code_bridge_services_tool_calls(ctx, task, workspace):
    """call_tool round-trips to the parent and comes back with a real result."""
    (workspace / "hello.txt").write_text("contents here", encoding="utf-8")
    code = (
        "r = call_tool('read_file', path='work/hello.txt')\n"
        "print('GOT:' + r['content'])\n"
    )
    r = run_code_action(ctx, task, code, timeout_s=30)
    assert r["returncode"] == 0, r["stderr"]
    assert "GOT:contents here" in r["stdout"]
    assert r["tool_calls"] == 1


def test_code_bridge_enforces_the_gate(ctx, task, workspace):
    """A call the gate refuses raises inside the sandbox, it does not run."""
    code = (
        "try:\n"
        "    call_tool('read_file', path='../../etc/passwd')\n"
        "    print('ALLOWED')\n"
        "except RuntimeError as e:\n"
        "    print('REFUSED:' + str(e))\n"
    )
    r = run_code_action(ctx, task, code, timeout_s=30)
    assert "REFUSED" in r["stdout"]
    assert "ALLOWED" not in r["stdout"]


def test_code_mode_collapses_turns(ctx, workspace, scripted):
    """Task touching 20 files: direct mode needs >=15 turns, code mode <=3,
    and both produce the same answer."""
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    for i in range(20):
        (workspace / f"f{i:02d}.txt").write_text(str(i), encoding="utf-8")

    def make_task(goal: str):
        return create_task(
            ctx.conn, ctx.log,
            TaskContract(goal=goal, artifact_scope=["work"],
                         success_criterion="json_schema",
                         output_schema={"type": "object", "required": ["total"],
                                        "properties": {"total": {"type": "integer"}}}),
            Budget(usd=5.0, seconds=600),
        )

    # --- code mode: one turn does all twenty reads through the bridge
    ctx.config.limits.max_steps = 24
    code_task = make_task("sum the twenty files [code]")
    scripted(
        "cloud",
        "<code>\n"
        "total = 0\n"
        "for i in range(20):\n"
        "    r = call_tool('read_file', path='work/f%02d.txt' % i)\n"
        "    total += int(r['content'])\n"
        "print(total)\n"
        "</code>",
        '<final>{"total": 190}</final>',
    )
    code_result = run_task(ctx, code_task.id)
    code_turns = ctx.conn.execute(
        "SELECT COUNT(*) FROM calls WHERE task_id=? AND purpose='step'", (code_task.id,)
    ).fetchone()[0]

    # --- direct mode: one read per turn
    direct_task = make_task("sum the twenty files [direct]")
    ctx.backends["cloud"].queue(
        *[
            '<action tool="read_file">' + json.dumps({"path": f"work/f{i:02d}.txt"}) + "</action>"
            for i in range(20)
        ],
        '<final>{"total": 190}</final>',
    )
    direct_result = run_task(ctx, direct_task.id)
    direct_turns = ctx.conn.execute(
        "SELECT COUNT(*) FROM calls WHERE task_id=? AND purpose='step'", (direct_task.id,)
    ).fetchone()[0]

    assert code_turns <= 3, f"code mode used {code_turns} turns"
    assert direct_turns >= 15, f"direct mode used only {direct_turns} turns"
    assert code_result.answer == direct_result.answer == {"total": 190}
