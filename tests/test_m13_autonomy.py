"""M13 acceptance — two touches, a read-only planner, and a deputy that
cannot widen anything.

The last two in the spec's list are the ones that catch real bugs: the
monitor-leak test and the plan-pinning test.
"""

from __future__ import annotations

import json

import pytest

from agent.execute.loop import run_task
from agent.kernel.integrity import IntegrityError
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.plan.planner import approve_plan, draft_plan, verify_plan_pin
from agent.plan.review import ReviewDecision, ReviewRequest, auto_review, parse_decision

PLAN_JSON = json.dumps(
    {
        "objective": "write the file",
        "steps": [
            {
                "id": "1",
                "intent": "write work/out.txt",
                "action_mode": "direct",
                "tools": ["write_file"],
                "artifact_scope": ["work"],
                "est_tokens": 500,
                "est_seconds": 5,
                "est_cost_usd": 0.002,
                "success_criterion": "always_pass",
                "reversible": True,
                "depends_on": [],
            }
        ],
        "human_approvals_expected": [],
        "open_questions": [],
        "unknowns": [],
    }
)


def _planned_task(ctx, workspace):
    task = create_task(
        ctx.conn,
        ctx.log,
        TaskContract(goal="write work/out.txt saying hello", artifact_scope=["work"]),
        Budget(usd=1.0, seconds=600),
    )
    ctx.backends["cloud"].queue(PLAN_JSON)
    plan = draft_plan(ctx, task)
    return task, plan


def test_two_touch_completion(ctx, workspace):
    """Objective -> plan -> approve -> completion, with exactly ONE human
    interaction between submission and result."""
    task, plan = _planned_task(ctx, workspace)
    approve_plan(ctx, task, plan)  # human touch #1

    ctx.backends["cloud"].queue(
        '<action tool="write_file">'
        + json.dumps({"path": "work/out.txt", "content": "hello"})
        + "</action>",
        '<final>{"answer": "written"}</final>',
    )
    ctx.backends["review"].queue("VERDICT: approve\nBECAUSE: write inside artifact_scope")

    result = run_task(ctx, task.id)  # human touch #2 is reading this

    assert result.status == "done"
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "hello"
    # Nothing parked in between: no third touch.
    assert ctx.pending_approvals(task.id) == []
    approvals = ctx.conn.execute(
        "SELECT COUNT(*) FROM events WHERE task_id=? AND type='human.approved'",
        (task.id,),
    ).fetchone()[0]
    assert approvals == 0


def test_planner_cannot_write(ctx, workspace):
    """During the plan phase, every write tool is denied at the gate."""
    from agent.security.capabilities import gate_tool_call

    task = create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="g", artifact_scope=["work"]),
        Budget(usd=1.0, seconds=600),
    )
    ctx.phase = "plan"
    try:
        for name in ("write_file", "run_code", "send_message", "git_push"):
            d = gate_tool_call(ctx, task, ctx.tools.get(name),
                               {"path": "work/x.txt", "content": "y"}, phase="plan")
            assert d.verdict == "deny", name
            assert "read-only" in d.reason
        d_read = gate_tool_call(ctx, task, ctx.tools.get("read_file"),
                                {"path": "work"}, phase="plan")
        assert d_read.verdict == "allow"
    finally:
        ctx.phase = "execute"


def test_plan_phase_restores_execute_phase(ctx, workspace):
    _planned_task(ctx, workspace)
    assert ctx.phase == "execute"


def test_auto_review_is_different_backend(ctx, task):
    """Assert the reviewer backend != the executing backend."""
    assert ctx.config.autonomy.auto_review_backend != "cloud"
    ctx.executing_backend = ctx.config.autonomy.auto_review_backend
    with pytest.raises(AssertionError, match="different backend"):
        auto_review(ctx, ReviewRequest(task_id=task.id, tool="write_file",
                                       args={"path": "work/x"}))


def test_auto_review_cannot_widen_scope(ctx, workspace, scripted):
    """An approve verdict never expands artifact_scope or enables network."""
    task = create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="try to escape", artifact_scope=["work"]),
        Budget(usd=1.0, seconds=600),
    )
    scripted(
        "cloud",
        '<action tool="write_file">'
        + json.dumps({"path": "../escaped.txt", "content": "out"})
        + "</action>",
        '<final>{"answer": "blocked"}</final>',
    )
    # The reviewer says yes to everything. It still cannot widen scope, because
    # the gate refuses before the reviewer is ever consulted.
    ctx.backends["review"].queue("VERDICT: approve\nBECAUSE: looks fine to me")

    run_task(ctx, task.id)

    assert not (workspace.parent / "escaped.txt").exists()
    reviews = ctx.conn.execute(
        "SELECT COUNT(*) FROM review_decisions WHERE task_id=?", (task.id,)
    ).fetchone()[0]
    assert reviews == 0, "an out-of-scope path should never reach the reviewer"


def test_trusted_tier_always_parks(ctx, workspace, scripted):
    """A TRUSTED action escalates to a human even with auto_review on."""
    task = create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="push it", artifact_scope=["work"]),
        Budget(usd=1.0, seconds=600),
    )
    scripted("cloud", '<action tool="git_push">{}</action>')
    result = run_task(ctx, task.id)

    assert result.status == "paused"
    assert result.approval_id
    pending = ctx.pending_approvals(task.id)
    assert len(pending) == 1 and pending[0]["tool"] == "git_push"
    # The deputy was never asked for a real verdict.
    assert ctx.conn.execute(
        "SELECT COUNT(*) FROM review_decisions WHERE task_id=? AND verdict!='escalate'",
        (task.id,),
    ).fetchone()[0] == 0


def test_denial_returns_rationale_not_error(ctx, workspace, scripted):
    """The agent receives the rationale plus the no-circumvention instruction."""
    from agent.plan.review import NO_CIRCUMVENTION

    task = create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="write something", artifact_scope=["work"]),
        Budget(usd=1.0, seconds=600),
    )
    scripted(
        "cloud",
        '<action tool="write_file">'
        + json.dumps({"path": "work/a.txt", "content": "x"})
        + "</action>",
        '<final>{"answer": "gave up, as instructed"}</final>',
    )
    ctx.backends["review"].queue(
        "VERDICT: deny\nBECAUSE: policy rule 'no writes during a freeze'"
    )

    run_task(ctx, task.id)

    second_prompt = ctx.backends["cloud"].prompts[1]
    assert "Reviewer denied" in second_prompt
    assert "no writes during a freeze" in second_prompt
    assert NO_CIRCUMVENTION[:60] in second_prompt
    assert not (workspace / "a.txt").exists()


def test_unparseable_reviewer_output_denies(ctx):
    """False means reject, not approve. A garbled reviewer is a denial."""
    d = parse_decision("I think that's probably fine?")
    assert d.verdict == "deny"
    d2 = parse_decision("VERDICT: approve\nBECAUSE: rule 2")
    assert d2.verdict == "approve"


def test_missing_policy_file_denies(ctx):
    from agent.plan.review import load_policy

    ctx.config.autonomy.auto_review_policy = "./does-not-exist.md"
    text = load_policy(ctx)
    assert "Deny every action" in text


def test_review_decision_reaches_the_agent_as_guidance(ctx):
    d = ReviewDecision(verdict="deny", rationale="policy rule 4")
    msg = d.to_agent()
    assert "policy rule 4" in msg and "another route" in msg


def test_plan_hash_pinned_after_approval(ctx, workspace):
    """Mutate the plan post-approval. Assert execution halts."""
    task, plan = _planned_task(ctx, workspace)
    approve_plan(ctx, task, plan)
    assert verify_plan_pin(ctx, task).id == plan.id

    tampered = json.loads(json.dumps(plan.body()))
    tampered["steps"][0]["artifact_scope"] = ["/"]  # quietly widen the scope
    ctx.conn.execute(
        "UPDATE plans SET body_json=? WHERE id=? AND task_id=?",
        (json.dumps(tampered), plan.id, task.id),
    )
    ctx.conn.commit()

    with pytest.raises(IntegrityError, match="modified after approval"):
        verify_plan_pin(ctx, task)

    ctx.config.autonomy.plan_required = True
    with pytest.raises(IntegrityError):
        run_task(ctx, task.id)


def test_plan_renders_the_honesty_fields(ctx, workspace):
    task, plan = _planned_task(ctx, workspace)
    plan.human_approvals_expected = ["3"]
    plan.open_questions = ["which branch?"]
    text = plan.seal().render()
    assert "will PARK for you at: 3" in text
    assert "which branch?" in text

    plan.human_approvals_expected = []
    assert "runs unattended end to end" in plan.seal().render()


def test_deviation_bands(ctx, workspace):
    from agent.plan.deviation import on_deviation

    task, plan = _planned_task(ctx, workspace)
    small = on_deviation(ctx, task, plan, "1", "file was slightly larger")
    assert small.action == "proceed"
    big = on_deviation(
        ctx, task, plan, "1",
        "permission denied and the config does not exist; needs a different approach",
    )
    assert big.action == "park_for_replan"
    rows = ctx.conn.execute(
        "SELECT COUNT(*) FROM deviations WHERE task_id=?", (task.id,)
    ).fetchone()[0]
    assert rows == 2


def test_client_stream_maps_and_filters(ctx, task):
    from agent.control.events import stream_events, to_client_event

    ctx.log.append(task.id, "tool.intent", {"tool": "read_file"})
    body = "".join(stream_events(ctx, task.id))
    assert "event: tool.called" in body
    assert to_client_event({"type": "verify.attempted", "seq": 1, "payload": {}}) is None
