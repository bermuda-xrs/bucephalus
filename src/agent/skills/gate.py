"""M9 — THE SKILL GATE. Built before M11. This ordering is not negotiable.

The evidence: measured across thousands of trajectories, human-curated skills
improved pass rates substantially (roughly +16 points), while self-generated
skills averaged slightly NEGATIVE (about -1 point). Even curated skills made
things worse on a fifth of tasks.

Build a generator without a gate and you ship a library that quietly degrades
the system while looking like progress.

Paired, not independent: run the SAME task both ways. This controls for task
difficulty and gives far more statistical power at small n, which matters when
each trial costs money.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field

from scipy.stats import binomtest

from ..kernel.types import Budget, GateDecision, TaskContract
from ..workspace import reset_workspace


@dataclass
class TaskSpec:
    """One paired-trial task. `key` identifies the pair, not the arm."""

    key: str
    goal: str
    success_criterion: str = "always_pass"
    output_schema: dict = field(default_factory=dict)
    artifact_scope: list[str] = field(default_factory=list)
    catastrophic: bool = False  # a regression here is a veto, not a data point
    # The task's declared input files. Both arms are rebuilt from these before
    # they run: the two arms of a pair share one workspace, so without a reset
    # the CONTROL arm can be graded against the answer the SKILL arm just
    # wrote — manufacturing precisely the lift this gate exists to detect.
    fixtures: dict = field(default_factory=dict)
    # Outside artifact_scope. See TaskContract.grader_dir.
    grader_dir: str | None = None


class ContaminationError(RuntimeError):
    """A trial task was one the skill was drafted from. That is a fake positive."""


def check_contamination(ctx, skill_id: str, task_key: str) -> None:
    """Never reuse a task the skill was drafted from.

    Enforced here rather than left as advice, because a contaminated suite
    produces a confident, significant, entirely fake lift.
    """
    row = ctx.conn.execute(
        "SELECT provenance_json FROM skills WHERE id=?", (skill_id,)
    ).fetchone()
    if not row:
        return
    drafted_from = set(json.loads(row[0]).get("drafted_from", []))
    if task_key in drafted_from:
        raise ContaminationError(
            f"task {task_key!r} was used to draft skill {skill_id[:12]}; "
            "using it as a trial would manufacture a fake positive"
        )


def run_paired_trial(ctx, skill_id: str, task_spec: TaskSpec, seed: int | None = None) -> None:
    """Run the same task with and without the skill. Randomize arm order."""
    check_contamination(ctx, skill_id, task_spec.key)
    arms = ["control", "skill"]
    random.Random(seed).shuffle(arms)

    results: dict[str, object] = {}
    for arm in arms:
        skills = ctx.baseline_skills()
        if arm == "skill":
            skills = skills + [ctx.skills.load(skill_id)]
        results[arm] = run_task_isolated(ctx, task_spec, skills)

    for arm in ("control", "skill"):
        res = results[arm]
        ctx.conn.execute(
            """INSERT INTO skill_trials(skill_id, task_key, arm, success, score,
                                        paid_tokens, free_tokens, wall_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                skill_id, task_spec.key, arm, int(res.success), res.score,
                res.paid_tokens, res.free_tokens, res.wall_ms, time.time(),
            ),
        )
    ctx.conn.commit()


@dataclass
class TrialOutcome:
    success: bool
    score: float
    paid_tokens: int
    free_tokens: int
    wall_ms: int
    task_id: str


def run_task_isolated(ctx, task_spec: TaskSpec, skills: list[dict]) -> TrialOutcome:
    """One arm of one pair, in its own task with its own frozen skill set."""
    from ..execute.loop import run_task
    from ..kernel.tasks import create_task

    # Identical bytes for both arms. Must happen INSIDE the arm, not once per
    # pair, or the second arm inherits the first arm's output.
    reset_workspace(ctx.config.root, task_spec.artifact_scope, task_spec.fixtures)

    contract = TaskContract(
        goal=task_spec.goal,
        output_schema=task_spec.output_schema,
        success_criterion=task_spec.success_criterion,
        grader_dir=task_spec.grader_dir,
        artifact_scope=task_spec.artifact_scope,
    )
    budget = Budget(
        usd=ctx.config.limits.default_task_budget_usd,
        seconds=ctx.config.limits.default_task_budget_seconds,
    )
    task = create_task(ctx.conn, ctx.log, contract, budget)
    ctx.set_frozen_skills(task.id, skills)
    t0 = time.time()
    result = run_task(ctx, task.id)
    m = ctx.telemetry.metrics(task.id)
    return TrialOutcome(
        success=result.status == "done",
        score=1.0 if result.status == "done" else 0.0,
        paid_tokens=m.paid_tokens,
        free_tokens=m.free_tokens,
        wall_ms=int((time.time() - t0) * 1000),
        task_id=task.id,
    )


def fetch_paired_outcomes(
    conn, skill_id: str, task_keys: set[str] | None = None
) -> list[tuple[str, int, int]]:
    """[(task_key, control_success, skill_success)] for complete pairs only.

    `task_keys` restricts to a subset of the trials ALREADY RUN. That is how
    a contamination-clean sub-analysis is reported without spending the
    compute twice: the eval tasks are a subset of the full suite, so the same
    stored trials answer both questions.

    Note what this does NOT do: it never counts one task twice. Rows are
    collapsed by task_key, so re-running a task under another seed replaces
    its outcome rather than adding a pair. Two runs of one task share its
    underlying difficulty; counting them as two pairs would inflate n without
    adding information and make the p-value anti-conservative.
    """
    rows = conn.execute(
        """SELECT task_key, arm, success FROM skill_trials
           WHERE skill_id=? ORDER BY task_key""",
        (skill_id,),
    ).fetchall()
    by_key: dict[str, dict[str, int]] = {}
    for key, arm, success in rows:
        if task_keys is not None and key not in task_keys:
            continue
        by_key.setdefault(key, {})[arm] = success
    return [
        (k, v["control"], v["skill"])
        for k, v in sorted(by_key.items())
        if "control" in v and "skill" in v
    ]


def evaluate_skill(
    ctx,
    skill_id: str,
    catastrophic_keys: set[str] | None = None,
    task_keys: set[str] | None = None,
) -> GateDecision:
    """All four conditions must hold: enough pairs, enough discordant pairs,
    lift above threshold, and significance.

    Statistical significance alone is not enough — a +0.5pp result that is
    significant is not worth the context tokens the skill occupies.
    """
    pairs = fetch_paired_outcomes(ctx.conn, skill_id, task_keys)
    n = len(pairs)
    cfg = ctx.config.gate
    catastrophic_keys = catastrophic_keys or set()

    # Computed before any early return. A verdict of "insufficient" that
    # reports nothing but n leaves the reader unable to see WHICH way an
    # underpowered result pointed, which is most of its remaining value.
    b = sum(1 for _, c, s in pairs if s == 1 and c == 0)   # skill wins
    c_ = sum(1 for _, c, s in pairs if s == 0 and c == 1)  # control wins
    discordant = b + c_
    lift_pp = 100.0 * (b - c_) / n if n else 0.0
    p = (binomtest(b, discordant, 0.5, alternative="greater").pvalue
         if discordant else 1.0)
    stats = {"n": n, "b": b, "c": c_, "discordant": discordant,
             "lift_pp": round(lift_pp, 2), "p": round(p, 5)}

    if n < cfg.min_paired_trials:
        return GateDecision.insufficient(
            f"only {n} paired trials, need {cfg.min_paired_trials}", **stats
        )

    if discordant < cfg.min_discordant_pairs:
        return GateDecision.insufficient(
            f"only {discordant} discordant pairs, need "
            f"{cfg.min_discordant_pairs}",
            **stats,
        )

    # A task the control passed and the skill failed is worse than a task
    # neither passed. Veto on any severe case.
    for key, cc, ss in pairs:
        if cc == 1 and ss == 0 and key in catastrophic_keys:
            return GateDecision.reject(
                f"catastrophic regression on {key}", **stats
            )

    if lift_pp >= cfg.min_lift_pp and p < cfg.max_p_value:
        # M7.4 rule 4 — a skill that shines on the checks it could see and
        # not on the ones it could not is the signature of fitting the
        # checker rather than doing the task. Refuse rather than warn: this
        # is the failure mode the whole gate exists to catch.
        gap = _holdout_gap(ctx, skill_id)
        if gap is not None:
            suspicion = ctx.mesh.suspicion_signal(*gap)
            if suspicion:
                return GateDecision.reject(
                    f"suspected gaming — {suspicion}",
                    visible_pass=gap[0], holdout_pass=gap[1],
                    gaming_check="ran", **stats,
                )
            return GateDecision.promote(gaming_check="ran", **stats)

        # M7.4 rule 4 DID NOT RUN, and a promotion must say so rather than
        # look like it passed a check that never happened.
        #
        # The holdout-gap recorder below has no callers. The only code that
        # partitions visible/held-out checks is the M11 generator
        # (holdout_frac=0.30), which has never run. A `verify_program` suite
        # has one grader per task and no partition at all, so there is no gap
        # to compute and the veto is inoperative for it.
        #
        # That matters most for exactly the suites it cannot check: the
        # grader holds the answer, and until it moved outside artifact_scope
        # a skill reading it would have scored 100% with nothing watching.
        return GateDecision.promote(
            gaming_check="NOT MEASURED — no visible/held-out partition "
                         "exists for this suite, so M7.4 rule 4 was skipped",
            **stats)

    return GateDecision.reject(
        f"lift={lift_pp:.1f}pp p={p:.3f} n={n}", **stats
    )


def _holdout_gap(ctx, skill_id: str) -> tuple[float, float] | None:
    """(visible_pass_rate, holdout_pass_rate), or None if not measured.

    Recorded by whatever runs the checks; absent it, the gate simply has no
    gaming signal to act on and says so rather than inventing one.
    """
    row = ctx.conn.execute(
        "SELECT value_json FROM semantic_cache WHERE key=?",
        (f"holdout_gap:{skill_id}",),
    ).fetchone()
    if not row:
        return None
    try:
        d = json.loads(row[0])
        return float(d["visible"]), float(d["holdout"])
    except (KeyError, ValueError, TypeError):
        return None


def record_holdout_gap(ctx, skill_id: str, visible: float, holdout: float) -> None:
    """Store the visible/held-out pass rates so the gate can see the gap."""
    import time as _t

    ctx.conn.execute(
        "INSERT OR REPLACE INTO semantic_cache(key,value_json,taint,source_hash,"
        "created_at,hits) VALUES (?,?,?,?,?,0)",
        (f"holdout_gap:{skill_id}",
         json.dumps({"visible": visible, "holdout": holdout}),
         "trusted", skill_id, _t.time()),
    )
    ctx.conn.commit()


def promote(ctx, skill_id: str, decision: GateDecision, human_approved: bool) -> None:
    """M8.4 rule 6 — promotion is a TRUSTED capability requiring human approval.

    Only the gate promotes, and only with a human saying yes. The generator
    (M11) cannot reach this function.
    """
    if decision.verdict != "promote":
        raise ValueError(f"gate did not promote: {decision.reason}")
    if not human_approved:
        raise PermissionError(
            "promoting a skill is a trusted-tier action and requires human approval"
        )
    ctx.skills.set_status(skill_id, "promoted")
    meta = ctx.skills.load(skill_id, verify=False)
    ctx.record_promotion(skill_id, decision.detail | {"name": meta["name"]})


# -- M9.4 retrieval, measured separately -----------------------------------


def measure_retrieval_precision(ctx, eval_set: list[tuple[str, set[str]]]) -> float:
    """eval_set: [(task_goal, correct_skill_ids)].

    Failures often happen because the right skill existed and was not
    retrieved. Measure selection separately from quality, or you will blame
    good skills for a bad index.
    """
    from .retrieve import retrieve

    if not eval_set:
        return 0.0
    hits = 0
    for goal, correct in eval_set:
        chosen = {s["id"] for s in retrieve(ctx, goal)}
        if chosen & correct:
            hits += 1
    return hits / len(eval_set)


# -- M9.5 decay and pruning -------------------------------------------------


def decay_pass(ctx, days: int = 30, drop: float = 0.10) -> list[dict]:
    """A shrinking library is healthy. Demoted skills leave the prefix immediately."""
    actions = []
    cutoff = time.time() - days * 86400
    for s in ctx.skills.promoted():
        rows = ctx.conn.execute(
            """SELECT success FROM skill_invocations
               WHERE skill_id=? AND created_at>=?""",
            (s["id"], cutoff),
        ).fetchall()
        if not rows:
            ctx.skills.set_status(s["id"], "demoted")
            actions.append({"skill": s["name"], "action": "demoted", "why": "unused"})
            continue
        rate = sum(r[0] for r in rows) / len(rows)
        baseline = _promoted_success_rate(ctx, s["id"])
        if baseline is not None and rate < baseline - drop:
            ctx.skills.set_status(s["id"], "demoted")
            actions.append(
                {"skill": s["name"], "action": "demoted",
                 "why": f"regression {rate:.2f} vs {baseline:.2f}"}
            )
    return actions


def _promoted_success_rate(ctx, skill_id: str) -> float | None:
    rows = ctx.conn.execute(
        "SELECT success FROM skill_trials WHERE skill_id=? AND arm='skill'", (skill_id,)
    ).fetchall()
    if not rows:
        return None
    return sum(r[0] for r in rows) / len(rows)
