from __future__ import annotations

import json


from agent.execute.loop import run_task
from agent.execute.tools import SMALL_TOOLS


def _act(tool, **args):
    return f'<action tool="{tool}">{json.dumps(args)}</action>'


def test_the_small_profile_has_exactly_these_six_tools_the_sixth_so_it_can_open_what_is_elided():
    assert set(SMALL_TOOLS) == {"read_file", "write_file", "search", "run_command", "update_plan", "expand_artifact"}


def test_a_small_context_registers_only_those(small):
    assert small.tools.names() == sorted(SMALL_TOOLS)


def test_the_small_prefix_and_schemas_show_only_those(small, task, scripted):
    small.backends["cloud"].queue('<final>{"answer": "ok"}</final>')
    t = __import__("agent.kernel.tasks", fromlist=["create_task"]).create_task(
        small.conn, small.log, task.contract, task.budget)
    run_task(small, t.id)
    call = small.backends["cloud"].calls[0]
    assert sorted(s["name"] for s in call["tools"]) == sorted(SMALL_TOOLS)
    for gone in ("run_code", "memory_write", "web_fetch", "web_search", "delegate", "git_push", "glob", "grep"):
        assert f"{gone}(" not in call["prefix"], gone
    assert "<code>" not in call["prefix"], "no code-as-action on this tier"


def test_the_default_profile_is_unchanged_and_has_none_of_the_new_tools(ctx):
    names = ctx.tools.names()
    assert "run_code" in names and "memory_write" in names
    assert not {"search", "run_command", "update_plan"} & set(names)


def test_a_tool_outside_the_profile_is_not_callable(small, task):
    from agent.kernel.tasks import create_task

    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue(_act("memory_write", text="x"), '<final>{"answer": "ok"}</final>')
    run_task(small, t.id)
    assert "no such tool" in small.backends["cloud"].calls[1]["tail"]
    assert small.memory.entries("memory") == [] or all("x" != e.text for e in small.memory.entries("memory"))


def test_search_finds_contents_and_names(small, task, workspace):
    from agent.execute.tools import tool_search

    (workspace / "a.py").write_text("def alpha_one():\n    return 1\n", encoding="utf-8")
    (workspace / "b.txt").write_text("nothing here\n", encoding="utf-8")
    by_content = tool_search(small, task, pattern="alpha_one", path="work")
    assert "a.py" in json.dumps(by_content)
    by_name = tool_search(small, task, pattern="*.py", path="work", names=True)
    assert "a.py" in json.dumps(by_name) and "b.txt" not in json.dumps(by_name)
    only_py = tool_search(small, task, pattern="nothing", path="work", glob="*.py")
    assert "b.txt" not in json.dumps(only_py)


def test_update_plan_keeps_a_pinned_checklist_and_leaves_the_approved_plan_alone(small, task):
    from agent.kernel.tasks import create_task

    t = create_task(small.conn, small.log, task.contract, task.budget)
    items = [{"text": "read the file", "status": "done"}, {"text": "write the fix", "status": "doing"}]
    small.backends["cloud"].queue(_act("update_plan", items=items), '<final>{"answer": "ok"}</final>')
    run_task(small, t.id)
    tail = small.backends["cloud"].calls[1]["tail"]
    assert "[x] read the file" in tail and "[~] write the fix" in tail
    assert small.conn.execute("SELECT COUNT(*) FROM plans WHERE task_id=?", (t.id,)).fetchone()[0] == 0


def test_a_tool_the_policy_denies_cannot_be_reached_through_call_tool(ctx, task, workspace):
    from agent.execute.codeact import run_code_action
    from agent.security.exposure import ToolPolicy

    (workspace / "a.txt").write_text("secret\n", encoding="utf-8")
    ctx.restrict_tools(task.id, ToolPolicy(frozenset({"read_file"}), "test"))
    r = run_code_action(ctx, task, 'print(call_tool("read_file", path="work/a.txt"))')
    assert r["ok"] is False and "not available" in (r["stderr"] + r["stdout"]), r
