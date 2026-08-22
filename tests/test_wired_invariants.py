"""Invariants that existed only as unit-tested functions nothing called.

Each of these had a passing test and zero call sites in the running agent —
the same shape as the semantic cache that sat dead for a week. A test that
the function works is not a test that the system uses it.
"""

from __future__ import annotations

import json

import pytest

from agent.execute.loop import run_task
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract, VerifyResult


class DescendingVerifier:
    """Scores 0.9 then 0.2, never passing. The ratchet must keep the 0.9."""

    kind = "descending"

    def __init__(self, scores):
        self.scores = list(scores)
        self.i = 0

    def check(self, artifact, contract):
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
    """Without the ratchet a run that never passes returns nothing at all."""
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
    """Three identical scores means more turns buy nothing. On CPU that is minutes."""
    ctx.mesh.registry["descending"] = DescendingVerifier([0.5])
    ctx.config.limits.max_steps = 12
    t = _task(ctx, "descending")
    scripted("cloud", *['<final>{"a": 1}</final>'] * 12)

    result = run_task(ctx, t.id)
    assert result.status == "plateau"
    assert result.steps < 12, f"ran {result.steps} turns on a plateau"


def test_skill_invocations_are_recorded_so_decay_has_data(ctx, workspace, scripted):
    """M9.5 reads skill_invocations. Never writing there demotes every skill."""
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

    # And so decay no longer demotes a skill that is actually in use.
    actions = decay_pass(ctx)
    assert not any(a["skill"] == "useful" and a["why"] == "unused" for a in actions)


def test_decay_still_demotes_a_genuinely_unused_skill(ctx):
    from agent.skills.gate import decay_pass

    sid = ctx.skills.store("stale", "# stale\nwhen never\n\n## step\nx\n",
                           {"applies_when": "never"}, {})
    ctx.skills.set_status(sid, "promoted")
    assert any(a["why"] == "unused" for a in decay_pass(ctx))


def test_deviation_parks_a_materially_changed_task(ctx, workspace):
    """M13.4 — a big surprise is a human's call, not a silent retry."""
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

        def check(self, artifact, contract):
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


def test_gate_refuses_a_skill_that_games_the_visible_checks(ctx):
    """M7.4 rule 4 — a big visible/held-out gap is the signature of gaming."""
    import time

    from agent.skills.gate import evaluate_skill, record_holdout_gap

    sid = ctx.skills.store("gamer", "# gamer\nwhen x\n\n## step\ndo\n", {}, {})
    for i in range(30):
        ctrl, skl = (0, 1) if i < 20 else (0, 0)
        for arm, val in (("control", ctrl), ("skill", skl)):
            ctx.conn.execute(
                "INSERT INTO skill_trials(skill_id,task_key,arm,success,score,"
                "paid_tokens,free_tokens,wall_ms,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, f"k{i}", arm, val, float(val), 0, 0, 0, time.time()))
    ctx.conn.commit()

    assert evaluate_skill(ctx, sid).verdict == "promote"

    # Same trials, but it aced what it could see and failed what it could not.
    record_holdout_gap(ctx, sid, visible=0.95, holdout=0.40)
    d = evaluate_skill(ctx, sid)
    assert d.verdict == "reject" and "gaming" in d.reason


def test_no_gap_recorded_means_no_gaming_verdict(ctx):
    """Absent the measurement the gate says nothing rather than inventing it."""
    import time

    from agent.skills.gate import evaluate_skill

    sid = ctx.skills.store("clean", "# clean\nwhen x\n\n## step\ndo\n", {}, {})
    for i in range(30):
        ctrl, skl = (0, 1) if i < 20 else (0, 0)
        for arm, val in (("control", ctrl), ("skill", skl)):
            ctx.conn.execute(
                "INSERT INTO skill_trials(skill_id,task_key,arm,success,score,"
                "paid_tokens,free_tokens,wall_ms,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, f"k{i}", arm, val, float(val), 0, 0, 0, time.time()))
    ctx.conn.commit()
    assert evaluate_skill(ctx, sid).verdict == "promote"
