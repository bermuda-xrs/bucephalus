from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field

from math import comb

from ..kernel.types import Budget, GateDecision, TaskContract
from ..workspace import reset_workspace


def binom_greater(b: int, n: int) -> float:
    if n <= 0:
        return 1.0
    return sum(comb(n, k) for k in range(max(b, 0), n + 1)) / 2 ** n


@dataclass
class TaskSpec:
    key: str
    goal: str
    success_criterion: str = "always_pass"
    output_schema: dict = field(default_factory=dict)
    artifact_scope: list[str] = field(default_factory=list)
    catastrophic: bool = False
    fixtures: dict = field(default_factory=dict)
    grader_dir: str | None = None


class ContaminationError(RuntimeError):
    ...


GAMING_CHECK = ("not applicable - every check is held out, the grader is "
                "unreachable (SPEC-DELTA C1)")


def check_contamination(ctx, skill_id: str, task_key: str) -> None:
    row = ctx.conn.execute(
        "SELECT provenance_json FROM skills WHERE id=?", (skill_id,)
    ).fetchone()
    if not row:
        raise ContaminationError(
            f"no such skill {skill_id[:12]}: its drafting set is unknown")
    prov = json.loads(row[0])
    if prov.get("drafted_from") is None:
        raise ContaminationError(
            f"skill {skill_id[:12]} records no drafting set, so no trial can be "
            "certified uncontaminated. Declare it, once: alpha skills "
            f"--declare-drafted-from {skill_id} KEYS|none - 'none' is a human "
            "asserting it was drafted from none of the suite's tasks.")
    drafted_from = set(prov["drafted_from"])
    if task_key in drafted_from:
        raise ContaminationError(
            f"task {task_key!r} was used to draft skill {skill_id[:12]}; "
            "using it as a trial would manufacture a fake positive"
        )


def run_paired_trial(ctx, skill_id: str, task_spec: TaskSpec, seed: int | None = None) -> None:
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
    from ..execute.loop import run_task
    from ..kernel.tasks import create_task

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
        success=result.status in ("done", "done_unannounced"),
        score=1.0 if result.status in ("done", "done_unannounced") else 0.0,
        paid_tokens=m.paid_tokens,
        free_tokens=m.free_tokens,
        wall_ms=int((time.time() - t0) * 1000),
        task_id=task.id,
    )


def fetch_paired_outcomes(
    conn, skill_id: str, task_keys: set[str] | None = None
) -> list[tuple[str, int, int]]:
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
    pairs = fetch_paired_outcomes(ctx.conn, skill_id, task_keys)
    n = len(pairs)
    cfg = ctx.config.gate
    catastrophic_keys = catastrophic_keys or set()

    b = sum(1 for _, c, s in pairs if s == 1 and c == 0)
    c_ = sum(1 for _, c, s in pairs if s == 0 and c == 1)
    discordant = b + c_
    lift_pp = 100.0 * (b - c_) / n if n else 0.0
    p = binom_greater(b, discordant) if discordant else 1.0
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

    for key, cc, ss in pairs:
        if cc == 1 and ss == 0 and key in catastrophic_keys:
            return GateDecision.reject(
                f"catastrophic regression on {key}", **stats
            )

    if lift_pp >= cfg.min_lift_pp and p < cfg.max_p_value:
        return GateDecision.promote(gaming_check=GAMING_CHECK, **stats)

    return GateDecision.reject(
        f"lift={lift_pp:.1f}pp p={p:.3f} n={n}", **stats
    )


def promotion_rate(conn) -> dict:
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN status='promoted' THEN 1 ELSE 0 END),
             SUM(CASE WHEN status IN ('promoted','rejected','demoted') THEN 1 ELSE 0 END),
             COUNT(*)
           FROM skills"""
    ).fetchone()
    promoted, decided, total = (row[0] or 0), (row[1] or 0), (row[2] or 0)
    rate = (promoted / decided) if decided else 0.0
    if not decided:
        verdict = "no decisions yet"
    elif rate > 0.60:
        verdict = "gate too loose — check for contamination first"
    elif rate == 0.0 and decided >= 20:
        verdict = (
            "debug in this order: task suite not sensitive enough, skills too "
            "generic, grounding retrieval returning junk"
        )
    else:
        verdict = "expected range"
    return {"promoted": promoted, "decided": decided, "total": total,
            "rate": round(rate, 3), "verdict": verdict}


def promote(ctx, skill_id: str, decision: GateDecision, human_approved: bool) -> None:
    if decision.verdict != "promote":
        raise ValueError(f"gate did not promote: {decision.reason}")
    if not human_approved:
        raise PermissionError(
            "promoting a skill is a trusted-tier action and requires human approval"
        )
    ctx.skills.set_status(skill_id, "promoted")
    meta = ctx.skills.load(skill_id, verify=False)
    ctx.record_promotion(skill_id, decision.detail | {"name": meta["name"]})


def decay_pass(ctx, days: int = 30, drop: float = 0.10) -> list[dict]:
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
