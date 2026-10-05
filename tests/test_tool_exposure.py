"""B3 — "the tool exists" is not "this agent may call it". Plus D18.

Until Phase 5 every agent saw one registry, and the capability gate was the
only thing between a model and any tool in it. A child opened to summarise a
document could call `git_push`; nothing but the gate's taint arithmetic stood
in the way, and taint arithmetic is about ARGUMENTS, not authority.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bench"))

from agent.config import BackendConfig, Config  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.delegate.contracts import (  # noqa: E402
    SubtaskPlan,
    may_delegate,
    run_delegated,
    scope_widens,
)
from agent.execute.loop import run_task  # noqa: E402
from agent.kernel.scope import task_tree, visible_tasks  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract, Taint  # noqa: E402
from agent.security.exposure import (  # noqa: E402
    CHILD_DENIED,
    UNRESTRICTED,
    ToolNotExposed,
    ToolPolicy,
    child_policy,
)


def make_ctx(tmp_path):
    (tmp_path / "work").mkdir(exist_ok=True)
    cfg = Config(
        profile="mixed",
        db_path=str(tmp_path / "t.db"),
        anchor_path=str(tmp_path / "chain.anchor"),
        skills_path=str(tmp_path / "skills"),
        backends={"cloud": BackendConfig(kind="mock", model="m"),
                  "review": BackendConfig(kind="mock", model="r")},
        autonomy={"auto_review": False, "plan_required": False,
                  "approve_scoped_writes": True},
        root=str(tmp_path),
    )
    return AgentContext(cfg)


def new_task(ctx, goal="parent goal", scope=("work",), parent=None):
    return create_task(
        ctx.conn, ctx.log,
        TaskContract(goal=goal, success_criterion="always_pass",
                     artifact_scope=list(scope)),
        Budget(usd=1.0, seconds=120), parent_id=parent)


# ==========================================================================
# 5.1 / 5.3 — refused at dispatch, and logged
# ==========================================================================

def test_a_denied_tool_is_refused_at_dispatch_and_recorded(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    ctx.restrict_tools(t.id, ToolPolicy(frozenset({"write_file"}), "test"))
    ctx.backends["cloud"].queue(
        '<action tool="write_file">'
        + json.dumps({"path": "work/x.txt", "content": "hi"}) + "</action>",
        '<final>{"answer": "done"}</final>')
    run_task(ctx, t.id)

    assert not (tmp_path / "work" / "x.txt").exists(), "the write must not happen"
    rows = ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='tool.not_exposed'",
        (t.id,)).fetchall()
    assert rows, "the refusal must be on the chain — it is the auditable part"
    assert "write_file" in rows[0][0]
    ctx.close()


def test_the_tool_stays_in_the_manual_so_the_refusal_is_observable(tmp_path):
    """An agent that cannot SEE a tool works around its absence silently. An
    agent that is refused leaves a record of what it tried."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    ctx.restrict_tools(t.id, ToolPolicy(frozenset({"git_push"}), "test"))
    assert "git_push" in ctx.tools.manual(), (
        "hiding it would change the prefix per agent AND remove the evidence")
    ctx.close()


def test_exposure_is_checked_before_the_capability_gate(tmp_path):
    """Different questions, and the order decides what gets recorded. A child
    denied a tool should be refused for not being allowed it, not for whatever
    its arguments happened to be tainted with."""
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(
        encoding="utf-8")
    i = src.index("exposure_check(")
    j = src.index("gate_tool_call(ctx, task, tool, args", i - 2000)
    assert i < j, "exposure must be checked before the capability gate"


def test_an_unrestricted_agent_is_unaffected(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    assert ctx.tool_policy(t.id) is UNRESTRICTED
    ctx.backends["cloud"].queue(
        '<action tool="write_file">'
        + json.dumps({"path": "work/y.txt", "content": "hi"}) + "</action>",
        '<final>{"answer": "done"}</final>')
    run_task(ctx, t.id)
    assert (tmp_path / "work" / "y.txt").exists()
    ctx.close()


# ==========================================================================
# 5.2 — what a child loses, and the direction restrictions compose in
# ==========================================================================

def test_restrictions_compose_by_union_of_denials(tmp_path):
    parent = ToolPolicy(frozenset({"git_push"}), "parent")
    child = child_policy(parent)
    assert "git_push" in child.denied, (
        "a child must never reach a tool its parent could not")
    assert CHILD_DENIED <= child.denied
    grandchild = child_policy(child)
    assert child.denied <= grandchild.denied, "a chain may only narrow"


def test_a_policy_cannot_be_widened(tmp_path):
    """If a policy could be replaced rather than narrowed, an agent could
    delegate to itself and come back with more authority."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    ctx.restrict_tools(t.id, ToolPolicy(frozenset({"git_push", "run_code"}), "x"))
    with pytest.raises(ToolNotExposed, match="only narrow"):
        ctx.restrict_tools(t.id, ToolPolicy(frozenset({"git_push"}), "wider"))
    ctx.restrict_tools(t.id, ToolPolicy(frozenset({"git_push", "run_code",
                                                   "write_file"}), "narrower"))
    ctx.close()


def test_a_child_cannot_delegate_further(tmp_path):
    """Leaves cannot spawn. Checked from the TASK TREE rather than a flag, so
    it cannot be bypassed by constructing a context differently."""
    ctx = make_ctx(tmp_path)
    parent = new_task(ctx)
    child = new_task(ctx, "child goal", parent=parent.id)

    plan = SubtaskPlan(goal="deeper", estimated_irrelevant_tokens=99999,
                       output_schema={"type": "object"},
                       success_criterion="always_pass",
                       budget=Budget(usd=1.0, seconds=60))
    ok, why = may_delegate(ctx, child, plan)
    assert ok is False and "may not delegate further" in why

    ok, _ = may_delegate(ctx, parent, plan)
    assert ok is True, "the parent must still be able to delegate"
    ctx.close()


def test_a_child_cannot_widen_its_artifact_scope(tmp_path):
    ctx = make_ctx(tmp_path)
    parent = new_task(ctx, scope=("work",))
    plan = SubtaskPlan(goal="g", estimated_irrelevant_tokens=99999,
                       output_schema={"type": "object"},
                       success_criterion="always_pass",
                       budget=Budget(usd=1.0, seconds=60),
                       artifact_scope=["work", "anchor"])
    ok, why = may_delegate(ctx, parent, plan)
    assert ok is False and "never widen" in why

    narrower = SubtaskPlan(goal="g", estimated_irrelevant_tokens=99999,
                           output_schema={"type": "object"},
                           success_criterion="always_pass",
                           budget=Budget(usd=1.0, seconds=60),
                           artifact_scope=["work/sub"])
    ok, _ = may_delegate(ctx, parent, narrower)
    assert ok is True, "narrowing must still be allowed"
    ctx.close()


def test_scope_containment_is_by_path_not_by_string_prefix():
    """`work_other` is not inside `work`, though it starts with it."""
    assert scope_widens(["work"], ["work/sub"]) == []
    assert scope_widens(["work"], ["work_other"]) == ["work_other"]
    assert scope_widens(["work"], ["anchor"]) == ["anchor"]
    # Inverted by D29's sweep. This line pinned the fail-open reading - "no
    # parent scope means no limit" - which let a parent that declared nothing
    # hand a child any scope. A parent with no scope permits no path, so every
    # entry a child asks for widens.
    assert scope_widens([], ["anything"]) == ["anything"], "empty means nothing"


def test_a_real_delegated_child_gets_the_narrowed_policy(tmp_path):
    ctx = make_ctx(tmp_path)
    parent = new_task(ctx)
    ctx.restrict_tools(parent.id, ToolPolicy(frozenset({"git_push"}), "parent"))
    ctx.backends["cloud"].queue('<final>{"answer": "child done"}</final>',
                                '<final>{"answer": "parent done"}</final>')

    result = run_delegated(ctx, parent, SubtaskPlan(
        goal="summarise", estimated_irrelevant_tokens=99999,
        output_schema={"type": "object"}, success_criterion="always_pass",
        budget=Budget(usd=1.0, seconds=60)))

    policy = ctx.tool_policy(result.child_task_id)
    assert "git_push" in policy.denied, "the parent's denial is inherited"
    assert "memory_write" in policy.denied, "and the child defaults are added"
    assert "delegate" in policy.denied

    opened = ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='delegation.opened'",
        (parent.id,)).fetchone()
    assert "child_policy" in opened[0], "the child's authority is recorded"
    ctx.close()


def test_a_child_shares_its_parents_isolation_by_construction(tmp_path):
    """Isolation needs no separate inheritance: `visible_tasks` scopes to the
    TASK TREE and a child shares its parent's root, so it sees exactly what
    the parent sees and cannot widen it by existing."""
    ctx = make_ctx(tmp_path)
    ctx.config.isolate_retrieval = True
    parent = new_task(ctx)
    child = new_task(ctx, "child", parent=parent.id)
    other = new_task(ctx, "unrelated attempt")

    assert task_tree(ctx.conn, child.id) == {parent.id, child.id}
    assert visible_tasks(ctx, child) == visible_tasks(ctx, parent)
    assert other.id not in visible_tasks(ctx, child)
    ctx.close()


def test_a_child_cannot_write_tier_one_memory(tmp_path):
    ctx = make_ctx(tmp_path)
    parent = new_task(ctx)
    child = new_task(ctx, "child", parent=parent.id)
    ctx.restrict_tools(child.id, child_policy(UNRESTRICTED))

    ctx.backends["cloud"].queue(
        '<action tool="memory_write">'
        + json.dumps({"text": "a fact the child learned"}) + "</action>",
        '<final>{"answer": "done"}</final>')
    run_task(ctx, child.id)

    assert not ctx.conn.execute(
        "SELECT 1 FROM events WHERE task_id=? AND type='memory.written'",
        (child.id,)).fetchall(), (
        "a child opened to read one document has no business editing what "
        "the agent believes forever")
    assert ctx.conn.execute(
        "SELECT 1 FROM events WHERE task_id=? AND type='tool.not_exposed'",
        (child.id,)).fetchall()
    ctx.close()


# ==========================================================================
# 5.4 — D18: one door, and it will not open without a declared isolation
# ==========================================================================

def test_a_measurement_cannot_be_opened_without_declaring_isolation():
    from measurement import open_measurement

    import inspect
    sig = inspect.signature(open_measurement)
    p = sig.parameters["isolate"]
    assert p.default is inspect.Parameter.empty, (
        "isolate must have NO default: forgetting it should be a TypeError at "
        "the call, not an unrestricted run discovered three hours later")
    assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
        "keyword-only, so it cannot be passed by accident in the wrong slot")


def test_an_undeclared_context_is_refused(tmp_path):
    from measurement import IsolationNotDeclared, assert_isolation_declared

    ctx = make_ctx(tmp_path)
    assert ctx.config.isolate_retrieval is None, (
        "None means UNDECLARED and is not the same as False")
    with pytest.raises(IsolationNotDeclared, match="not declared"):
        assert_isolation_declared(ctx)

    ctx.config.isolate_retrieval = False
    assert_isolation_declared(ctx)      # declared, and legitimately off
    ctx.close()


def test_no_measurement_module_opens_a_context_for_itself():
    """The other half of making it structural rather than currently-true.

    D18 was that `ladder.py` and the calibration path declared no isolation
    while `noise.py` did. Three call sites remembering a flag is not a fix;
    one door that will not open without the declaration is.
    """
    offenders = []
    for f in sorted((ROOT / "bench").glob("*.py")):
        if f.name == "measurement.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr == "open"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "AgentContext"):
                offenders.append(f"{f.name}:{node.lineno}")
    assert not offenders, (
        f"these open a context directly instead of through "
        f"bench/measurement.open_measurement: {offenders}")


def test_every_measurement_entry_point_declares_one():
    """And declares it explicitly, so a reader can tell 'considered and not
    needed' from 'nobody thought about it'."""
    seen = {}
    for name in ("noise.py", "ladder.py", "run.py"):
        src = (ROOT / "bench" / name).read_text(encoding="utf-8")
        assert "open_measurement(" in src, f"{name} does not use the door"
        seen[name] = "isolate=True" in src or "isolate=False" in src
        assert seen[name], f"{name} calls the door without stating isolation"
    assert "isolate=True" in (ROOT / "bench" / "noise.py").read_text(
        encoding="utf-8"), "paired trials must be isolated"


TAINT_TABLE = {
    "append_to_outbox": (True, Taint.USER, Taint.TRUSTED),
    "edit_file": (True, Taint.USER, Taint.TRUSTED),
    "expand_artifact": (False, Taint.UNTRUSTED, Taint.UNTRUSTED),
    "git_push": (True, Taint.TRUSTED, Taint.TRUSTED),
    "glob": (False, Taint.UNTRUSTED, Taint.UNTRUSTED),
    "grep": (False, Taint.UNTRUSTED, Taint.UNTRUSTED),
    "list_dir": (False, Taint.UNTRUSTED, Taint.UNTRUSTED),
    "memory_consolidate": (True, Taint.USER, Taint.TRUSTED),
    "memory_read": (False, Taint.UNTRUSTED, Taint.TRUSTED),
    "memory_write": (True, Taint.USER, Taint.TRUSTED),
    "parallel": (False, Taint.UNTRUSTED, Taint.UNTRUSTED),
    "read_file": (False, Taint.UNTRUSTED, Taint.UNTRUSTED),
    "run_code": (True, Taint.USER, Taint.UNTRUSTED),
    "search_history": (False, Taint.UNTRUSTED, Taint.UNTRUSTED),
    "web_fetch": (False, Taint.UNTRUSTED, Taint.UNTRUSTED),
    "web_search": (False, Taint.UNTRUSTED, Taint.UNTRUSTED),
    "write_file": (True, Taint.USER, Taint.TRUSTED),
}


def test_every_registered_tool_has_a_taint_row():
    from agent.execute.tools import default_registry

    reg = default_registry()
    got = {n: (reg.get(n).side_effecting, reg.get(n).max_arg_taint, reg.get(n).output_taint)
           for n in reg.names()}
    assert got == TAINT_TABLE
