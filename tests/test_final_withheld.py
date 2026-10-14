from __future__ import annotations

import json

import pytest

from agent.execute import shadow
from agent.execute.loop import run_task
from agent.execute.tools import tool_read_file
from agent.kernel.tasks import create_task

JFINAL = '{"kind": "final", "answer": "ok"}'


def _act(tool, **args):
    return json.dumps({"kind": "action", "tool": tool, "args": args})


def _kinds(schema):
    return [b["properties"]["kind"]["const"] for b in schema["anyOf"]]


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


@pytest.fixture
def schema_small(small):
    small.backends["cloud"].cfg.trust_tier = "local"
    small.backends["cloud"].cfg.model_server = "ollama"
    small.config.limits.max_steps = 30
    return small


def _long(workspace, lines=3000):
    (workspace / "long.md").write_text("".join(f"line {i}: filler words to take up the room\n" for i in range(lines)),
                                       encoding="utf-8")


def test_while_pages_are_unread_the_schema_has_no_final_then_it_returns(schema_small, task, workspace):
    _long(workspace, 700)
    pages = tool_read_file(schema_small, task, "work/long.md")["pages"]
    assert 2 <= pages <= 3, "the test reads every page within the three held steps"
    t = create_task(schema_small.conn, schema_small.log, task.contract, task.budget)
    reads = [_act("read_file", path="work/long.md")] + [_act("read_file", path="work/long.md", page=n) for n in range(2, pages + 1)]
    schema_small.backends["cloud"].queue(*reads, JFINAL)
    assert run_task(schema_small, t.id).status.startswith("done")
    kinds = [_kinds(c["schema"]) for c in schema_small.backends["cloud"].calls]
    assert ["final" in k for k in kinds] == [False] + [False] * (pages - 1) + [True]
    assert [e["via"] for e in _events(schema_small, t, "pages.unread_held")] == ["schema"] * (pages - 1)


def test_the_final_branch_is_withheld_three_steps_at_most(schema_small, task, workspace):
    _long(workspace)
    t = create_task(schema_small.conn, schema_small.log, task.contract, task.budget)
    ls = _act("list_dir", path="work") if "list_dir" in schema_small.tools.names() else _act("search", pattern="zzz", path="work")
    schema_small.backends["cloud"].queue(_act("read_file", path="work/long.md"), ls, _act("search", pattern="q", path="work"),
                                         _act("search", pattern="r", path="work"), JFINAL)
    assert run_task(schema_small, t.id).status.startswith("done")
    kinds = [_kinds(c["schema"]) for c in schema_small.backends["cloud"].calls]
    assert ["final" in k for k in kinds] == [False, False, False, False, True]


def test_an_unverified_change_withholds_the_final_three_steps_at_most(schema_small, task, workspace):
    t = create_task(schema_small.conn, schema_small.log, task.contract, task.budget)
    s = _act("search", pattern="q", path="work")
    schema_small.backends["cloud"].queue(_act("write_file", path="work/a.py", content="x = 1\n"), s, s, s, JFINAL)
    assert run_task(schema_small, t.id).status.startswith("done")
    calls = schema_small.backends["cloud"].calls
    assert ["final" in _kinds(c["schema"]) for c in calls] == [False, False, False, False, True]
    assert "run_command" in calls[1]["tail"].rsplit("<observation", 1)[-1] or "run_command" in calls[1]["tail"][-600:]
    assert [e["via"] for e in _events(schema_small, t, "verify.continued")] == ["schema"] * 3


def test_a_backend_without_the_schema_keeps_the_reminders(small, task, workspace):
    _long(workspace)
    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue('<action tool="read_file">{"path": "work/long.md"}</action>', *['<final>{"answer": "ok"}</final>'] * 4)
    assert run_task(small, t.id).status.startswith("done")
    assert [e.get("via") for e in _events(small, t, "pages.unread_held")] == [None] * 3


@pytest.mark.skipif(not shadow.git_available(), reason="git is not installed")
def test_the_state_at_the_end_is_kept_for_reading_back(ctx, task, workspace):
    from agent.execute.shadow import ShadowRepo
    from agent.kernel.tasks import load_task

    ctx.config.mechanisms.shadow_checkpoints = True
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.backends["cloud"].queue('<action tool="write_file">{"path": "work/s.py", "content": "def f(): pass\\n"}</action>',
                                '<final>{"answer": "ok"}</final>')
    run_task(ctx, task.id)
    (workspace / "s.py").unlink()
    sha = _events(ctx, task, "workspace.final")[0]["sha"]
    repo = ShadowRepo.for_task(ctx, load_task(ctx.conn, task.id))
    assert repo._git("show", f"{sha}:work/s.py").stdout == "def f(): pass\n"
    assert all(c.get("sha") != sha for c in _events(ctx, task, "checkpoint.shadow")), "it is not a rewind point"
