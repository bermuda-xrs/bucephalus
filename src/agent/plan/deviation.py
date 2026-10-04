"""M13.4 — deviation handling.

The agent will discover things the plan did not anticipate. Three responses by
magnitude: note it and carry on, revise this step, or park and come back to
the human. That last one is a third touch — but only when the job genuinely
changed, which is exactly when you would want to know.

Log every deviation. A high deviation rate means the planner is
under-informed, which is fixable — usually too little read-only exploration
before planning.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

DeviationAction = Literal["proceed", "local_replan", "park_for_replan"]


@dataclass
class Deviation:
    action: DeviationAction
    drift: float
    reason: str = ""

    @classmethod
    def proceed(cls, drift: float = 0.0, reason: str = "") -> "Deviation":
        return cls("proceed", drift, reason)

    @classmethod
    def local_replan(cls, drift: float, reason: str = "") -> "Deviation":
        return cls("local_replan", drift, reason)

    @classmethod
    def park_for_replan(cls, drift: float, reason: str = "") -> "Deviation":
        return cls("park_for_replan", drift, reason)


def estimate_plan_drift(plan, step_id: str, discovery: str) -> float:
    """How far execution has departed from the approved plan, 0..1.

    Heuristic and deliberately legible: a discovery that names tools or paths
    the plan never mentioned is a bigger departure than one that does not.
    """
    step = plan.step(step_id) if plan else None
    if step is None:
        return 1.0

    signals = 0.0
    text = discovery.lower()

    known_scope = {s.lower() for s in step.artifact_scope}
    mentioned_paths = {
        w.strip(".,'\"") for w in text.split() if "/" in w or "\\" in w or w.endswith(".py")
    }
    if mentioned_paths and not (mentioned_paths & known_scope):
        signals += 0.4

    known_tools = {t.lower() for t in step.tools}
    for kw, weight in (
        ("does not exist", 0.3),
        ("missing", 0.2),
        ("permission denied", 0.35),
        ("denied", 0.3),
        ("failed", 0.15),
        ("not found", 0.25),
        ("unexpected", 0.2),
        ("irreversible", 0.5),
        ("different approach", 0.4),
    ):
        if kw in text:
            signals += weight
    if known_tools and any(t not in text for t in known_tools) and "instead" in text:
        signals += 0.2

    if not step.reversible:
        signals += 0.2

    return min(1.0, round(signals, 3))


def on_deviation(ctx, task, plan, step_id: str, discovery: str) -> Deviation:
    drift = estimate_plan_drift(plan, step_id, discovery)
    tolerance = ctx.config.autonomy.deviation_tolerance

    if drift < 0.10:
        dev = Deviation.proceed(drift, "minor surprise; noted")
    elif drift < tolerance:
        dev = Deviation.local_replan(drift, "revise this step only")
    else:
        dev = Deviation.park_for_replan(drift, "material change; a human decides")

    ctx.conn.execute(
        """INSERT INTO deviations(task_id, step_id, drift, action, discovery, created_at)
           VALUES (?,?,?,?,?,?)""",
        (task.id, step_id, drift, dev.action, discovery[:2000], time.time()),
    )
    ctx.conn.commit()
    ctx.log.append(
        task.id,
        "deviation.detected",
        {"step_id": step_id, "drift": drift, "action": dev.action},
    )
    return dev


