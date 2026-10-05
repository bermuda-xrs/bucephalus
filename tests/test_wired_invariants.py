from __future__ import annotations

import json


from agent.execute.loop import run_task
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract, VerifyResult


class DescendingVerifier:
    kind = "descending"

    def __init__(self, scores):
        self.scores = list(scores)
        self.i = 0

    def check(self, artifact, contract, root=None):
        s = self.scores[min(self.i, len(self.scores) - 1)]
        self.i += 1
        return VerifyResult(passed=False, score=s,
                            observation=f"scored {s}", verifier_id=self.kind)


def _task(ctx, criterion: str) -> object:
    return create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="produce something", success_criterion=criterion,
                     artifact_scope=["work"]),
        Budget(usd=1.0, seconds=600),
    )


def test_ratchet_returns_the_best_candidate_not_the_last(ctx, workspace, scripted):
    ctx.mesh.registry["descending"] = DescendingVerifier([0.9, 0.2, 0.2, 0.2])
    t = _task(ctx, "descending")
    scripted("cloud",
             '<final>{"attempt": "good-0.9"}</final>',
             '<final>{"attempt": "worse-0.2"}</final>',
             '<final>{"attempt": "worse-again"}</final>',
             '<final>{"attempt": "worse-again"}</final>')

    result = run_task(ctx, t.id)
    assert result.answer == {"attempt": "good-0.9"}, (
        "the loop kept a later, worse candidate instead of the best one"
    )


def test_plateau_stops_burning_turns(ctx, workspace, scripted):
    ctx.mesh.registry["descending"] = DescendingVerifier([0.5])
    ctx.config.limits.max_steps = 12
    t = _task(ctx, "descending")
    scripted("cloud", *['<final>{"a": 1}</final>'] * 12)

    result = run_task(ctx, t.id)
    assert result.status == "plateau"
    assert result.steps < 12, f"ran {result.steps} turns on a plateau"


def test_skill_invocations_are_recorded_so_decay_has_data(ctx, workspace, scripted):
    from agent.skills.gate import decay_pass

    sid = ctx.skills.store("useful", "# useful\nwhen testing\n\n## step\ndo it\n",
                           {"applies_when": "testing"}, {"drafted_by": "human"})
    ctx.skills.set_status(sid, "promoted")

    t = _task(ctx, "always_pass")
    ctx.set_frozen_skills(t.id, [ctx.skills.load(sid)])
    scripted("cloud", '<final>{"answer": "ok"}</final>')
    run_task(ctx, t.id)

    rows = ctx.conn.execute(
        "SELECT success FROM skill_invocations WHERE skill_id=?", (sid,)
    ).fetchall()
    assert rows, "the skill was used but no invocation was recorded"
    assert rows[0][0] == 1

    actions = decay_pass(ctx)
    assert not any(a["skill"] == "useful" and a["why"] == "unused" for a in actions)


def test_decay_still_demotes_a_genuinely_unused_skill(ctx):
    from agent.skills.gate import decay_pass

    sid = ctx.skills.store("stale", "# stale\nwhen never\n\n## step\nx\n",
                           {"applies_when": "never"}, {})
    ctx.skills.set_status(sid, "promoted")
    assert any(a["why"] == "unused" for a in decay_pass(ctx))


def test_deviation_parks_a_materially_changed_task(ctx, workspace):
    from agent.plan.planner import approve_plan, draft_plan

    plan_json = json.dumps({
        "objective": "write a file",
        "steps": [{"id": "1", "intent": "write work/out.txt", "action_mode": "direct",
                   "tools": ["write_file"], "artifact_scope": ["work"],
                   "success_criterion": "descending", "reversible": True,
                   "depends_on": [], "est_tokens": 10, "est_seconds": 1,
                   "est_cost_usd": 0.0}],
        "human_approvals_expected": [], "open_questions": [], "unknowns": [],
    })

    class Deviating:
        kind = "deviating"

        def check(self, artifact, contract, root=None):
            return VerifyResult(
                passed=False, score=0.1,
                observation=("permission denied and the config does not exist; "
                             "this needs a different approach entirely"),
                verifier_id=self.kind)

    ctx.mesh.registry["deviating"] = Deviating()
    t = create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="write work/out.txt", success_criterion="deviating",
                     artifact_scope=["work"]),
        Budget(usd=1.0, seconds=600))

    ctx.backends["cloud"].queue(plan_json)
    plan = draft_plan(ctx, t)
    approve_plan(ctx, t, plan)

    ctx.backends["cloud"].queue('<final>{"done": true}</final>',
                                '<final>{"done": true}</final>')
    result = run_task(ctx, t.id)

    assert result.status == "needs_replan"
    rows = ctx.conn.execute(
        "SELECT action FROM deviations WHERE task_id=?", (t.id,)
    ).fetchall()
    assert any(r[0] == "park_for_replan" for r in rows)


def test_a_promotion_says_the_gaming_veto_does_not_apply_and_why(ctx):
    import time

    from agent.skills.gate import GAMING_CHECK, evaluate_skill

    sid = ctx.skills.store("clean", "# clean\nwhen x\n\n## step\ndo\n", {}, {})
    for i in range(30):
        ctrl, skl = (0, 1) if i < 20 else (0, 0)
        for arm, val in (("control", ctrl), ("skill", skl)):
            ctx.conn.execute(
                "INSERT INTO skill_trials(skill_id,task_key,arm,success,score,"
                "paid_tokens,free_tokens,wall_ms,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, f"k{i}", arm, val, float(val), 0, 0, 0, time.time()))
    ctx.conn.commit()
    d = evaluate_skill(ctx, sid)
    assert d.verdict == "promote"
    assert d.detail.get("gaming_check") == GAMING_CHECK, d.detail
    for phrase in ("not applicable", "every check is held out",
                   "grader is unreachable", "C1"):
        assert phrase in GAMING_CHECK
