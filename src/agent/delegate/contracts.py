"""M10 — delegation, reluctantly.

The evidence is discouraging: across 1,600+ recorded runs on 7 frameworks,
failure rates ran 41-87%, with gains over a strong single agent often minimal
and failures concentrated in specification and coordination. One team's
multi-agent research system did win big — while using roughly 15x the tokens.

So: all five gates must pass. Fail any one, do it inline. If you cannot fill in
the contract, you do not understand the subtask well enough to hand it off.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..kernel.types import Budget, TaskContract
from ..security.exposure import child_policy


@dataclass
class SubtaskPlan:
    goal: str
    estimated_irrelevant_tokens: int = 0
    output_schema: dict | None = None
    success_criterion: str | None = None
    budget: Budget | None = None
    is_tightly_coupled: bool = False
    artifact_scope: list[str] = field(default_factory=list)


def scope_widens(parent_scope: list[str], child_scope: list[str]) -> list[str]:
    """Entries the child asked for that the parent does not already cover.

    Path containment, not string equality: a parent scoped to `work` covers a
    child asking for `work/sub`, and does not cover `work_other` — which a
    prefix comparison on strings would wrongly allow.
    """
    import os
    from pathlib import Path

    if not parent_scope:
        return []
    roots = [Path(os.path.abspath(x)) for x in parent_scope]
    out = []
    for entry in child_scope:
        p = Path(os.path.abspath(entry))
        inside = False
        for r in roots:
            try:
                p.relative_to(r)
                inside = True
                break
            except ValueError:
                if p == r:
                    inside = True
                    break
        if not inside:
            out.append(entry)
    return out


def may_delegate(ctx, task, subtask_plan: SubtaskPlan) -> tuple[bool, str]:
    """All five, plus the two structural refusals B3 adds.

    The five original gates ask whether delegating is WORTH it. The two below
    ask whether it is ALLOWED, and they come first because a refusal on
    authority should not depend on whether the estimate happened to clear a
    token threshold.
    """
    # LEAVES CANNOT SPAWN. Checked from the task tree rather than from a flag,
    # so it cannot be bypassed by constructing a context differently: if this
    # task has a parent, it is already a child.
    if getattr(task, "parent_id", None):
        return False, ("a delegated child may not delegate further; "
                       "unbounded depth is how a coordination failure becomes "
                       "a fork bomb")
    # ARTIFACT SCOPE IS INHERITED, NEVER WIDENED.
    widened = scope_widens(task.contract.artifact_scope,
                           subtask_plan.artifact_scope or [])
    if widened:
        return False, (f"child asked for artifact_scope {widened} which the "
                       f"parent's {task.contract.artifact_scope} does not "
                       "cover; a child may narrow its scope, never widen it")
    if subtask_plan.estimated_irrelevant_tokens < ctx.config.limits.delegation_min_isolated_tokens:
        return False, "insufficient isolation benefit"
    if subtask_plan.output_schema is None:
        return False, "no typed output schema"
    if subtask_plan.success_criterion not in ctx.mesh.registry:
        return False, "no checkable success criterion"
    if subtask_plan.budget is None:
        return False, "no budget"
    if subtask_plan.is_tightly_coupled:
        return False, "tightly coupled — never delegate"
    return True, "ok"


@dataclass
class DelegationResult:
    artifact_id: str  # written to the ledger by the child
    summary: str  # <=200 tokens, bounded
    verified: bool
    tokens_used: int
    child_task_id: str = ""
    # NO transcript. Not optional. The transcript is the thing you delegated
    # to avoid reading.

    def render(self) -> str:
        status = "verified" if self.verified else "UNVERIFIED"
        return (
            f"[delegation → artifact {self.artifact_id} · {status} · "
            f"{self.tokens_used} tokens]\n{self.summary}"
        )


SUMMARY_TOKEN_CAP = 200


def run_delegated(ctx, parent_task, subtask_plan: SubtaskPlan) -> DelegationResult:
    """Open a child task, run it, and bring back a reference plus a bounded summary.

    Children write to the ledger; parents receive references, never transcripts.
    """
    from ..execute.loop import run_task
    from ..kernel.tasks import create_task
    from ..kernel.types import Taint
    from ..ledger.artifacts import write_artifact

    ok, why = may_delegate(ctx, parent_task, subtask_plan)
    if not ok:
        raise ValueError(f"delegation refused: {why}")

    contract = TaskContract(
        goal=subtask_plan.goal,
        output_schema=subtask_plan.output_schema or {},
        success_criterion=subtask_plan.success_criterion or "always_pass",
        artifact_scope=subtask_plan.artifact_scope or parent_task.contract.artifact_scope,
        max_taint_in=parent_task.contract.max_taint_in,
    )
    child = create_task(
        ctx.conn, ctx.log, contract, subtask_plan.budget, parent_id=parent_task.id
    )

    # B3. The child's authority is the parent's, narrowed. Union of denials,
    # so a chain of delegations can only ever get smaller.
    #
    # Isolation needs no separate inheritance: `visible_tasks` scopes to the
    # TASK TREE, and a child shares its parent's root by construction, so it
    # sees exactly what the parent sees and cannot widen it by existing.
    # Asserted in tests/test_tool_exposure.py rather than assumed.
    policy = child_policy(ctx.tool_policy(parent_task.id))
    ctx.restrict_tools(child.id, policy)

    ctx.log.append(
        parent_task.id,
        "delegation.opened",
        {"child_task_id": child.id, "goal": subtask_plan.goal[:200],
         "child_policy": policy.describe()},
    )

    result = run_task(ctx, child.id)

    answer_text = (
        result.answer
        if isinstance(result.answer, str)
        else json.dumps(result.answer or {}, indent=2)
    )
    aid = write_artifact(
        ctx.conn, parent_task.id, "answer", answer_text, Taint.USER,
        {"from_child": child.id},
    )
    summary = _bound(answer_text, SUMMARY_TOKEN_CAP)
    metrics = ctx.telemetry.metrics(child.id)

    ctx.log.append(
        parent_task.id,
        "delegation.closed",
        {
            "child_task_id": child.id,
            "artifact_id": aid,
            "verified": result.status == "done",
            "tokens": metrics.paid_tokens + metrics.free_tokens,
        },
    )
    return DelegationResult(
        artifact_id=aid,
        summary=summary,
        verified=result.status == "done",
        tokens_used=metrics.paid_tokens + metrics.free_tokens,
        child_task_id=child.id,
    )


def _bound(text: str, max_tokens: int) -> str:
    from ..tokens import count_tokens

    if count_tokens(text) <= max_tokens:
        return text
    # Cut on a character budget derived from the token cap, then say so.
    approx_chars = int(max_tokens * 3.4)
    return text[:approx_chars].rstrip() + f"\n[... bounded to {max_tokens} tokens ...]"
