"""M13.0-13.2 — drafting, storing, approving, and pinning a plan.

Planning runs READ-ONLY. The planner may read files, search, and inspect, but
every write tool is denied during the plan phase — enforced in the capability
gate, not by prompt instruction. A planner that can edit while planning
defeats the entire point of plan mode.

The approved plan hash is written to the anchor. Execution verifies it before
every step; a plan cannot be modified after approval.
"""

from __future__ import annotations

import json
import re
import time

from ..kernel.integrity import IntegrityError
from ..kernel.types import Taint
from ..ledger.artifacts import write_artifact
from .types import Plan, PlanStep

PLANNER_PREFIX = """\
You are the planner. You produce a plan and nothing else.

You are running READ-ONLY: every write tool is denied to you right now, in
code. Do not plan around that — it is the point. Your job is to surface a
misunderstanding before the first change is made.

Output exactly one JSON object, no prose around it:

{
  "objective": "one line restating what the human wants",
  "steps": [
    {
      "id": "1",
      "intent": "one human-readable line",
      "action_mode": "direct" | "code" | "delegate",
      "tools": ["read_file"],
      "artifact_scope": ["relative/path"],
      "est_tokens": 2000,
      "est_seconds": 20,
      "est_cost_usd": 0.01,
      "success_criterion": "a registered verifier name",
      "reversible": true,
      "depends_on": []
    }
  ],
  "human_approvals_expected": ["3"],
  "open_questions": ["anything ambiguous — ask it NOW, not mid-run"],
  "unknowns": ["what you could not determine by reading"]
}

Rules:
  * action_mode "code" only when a step chains 3+ tool calls, iterates, or
    branches. A single atomic action is "direct" — code adds risk for nothing.
  * success_criterion must name a verifier that exists. If unsure, use
    "always_pass" and say so in unknowns.
  * human_approvals_expected lists the step ids that WILL park for a human.
    Be honest. Someone is deciding whether to walk away based on this.
  * open_questions is where ambiguity goes. Every question you do not ask here
    becomes an interruption at 2am."""


def draft_plan(ctx, task, backend_name: str = "cloud") -> Plan:
    """Draft a plan with a read-only backend. The planner never writes."""
    backend = ctx.backends[backend_name]
    ctx.phase = "plan"  # the capability gate reads this
    try:
        context = _read_only_survey(ctx, task)
        tail = (
            f"<objective>\n{task.contract.goal}\n</objective>\n\n"
            f"<artifact_scope>{json.dumps(task.contract.artifact_scope)}</artifact_scope>\n"
            f"<available_verifiers>{json.dumps(sorted(ctx.mesh.registry))}</available_verifiers>\n"
            f"<available_tools>{json.dumps(ctx.tools.names())}</available_tools>\n"
            f"<budget>${task.budget.usd:.2f} / {task.budget.seconds}s</budget>\n\n"
            f"<survey>\n{context}\n</survey>"
        )
        resp = ctx.call_model(
            task, backend, "plan", prefix=PLANNER_PREFIX, tail=tail, tools=None,
            max_tokens=4096, temperature=0.0,
        )
        plan = parse_plan(resp.text, fallback_objective=task.contract.goal)
    finally:
        ctx.phase = "execute"
    return store_plan(ctx, task, plan)


def _read_only_survey(ctx, task) -> str:
    """What the planner is allowed to look at before planning. Reads only.

    Read-only is not unrestricted (AUDIT F5, D43). This read goes around
    `gate_tool_call`, so it applies the gate's own protected-path check: a
    scope that is, contains, or lies inside the agent's state - secrets,
    databases, graders - is named as refused, never read or listed.
    """
    from ..security.capabilities import is_protected, task_state_paths

    state = task_state_paths(ctx, task)
    out = []
    for scope in task.contract.artifact_scope[:5]:
        p = ctx.config.resolve(scope)
        if is_protected(str(scope), ctx.config.root, state):
            out.append(f"{scope}: protected state - not read (the gate refuses it too)")
        elif p.is_dir():
            names = sorted(x.name for x in list(p.iterdir())[:80])
            out.append(f"{scope}/ -> {', '.join(names)}")
        elif p.is_file():
            head = p.read_text(encoding="utf-8", errors="replace")[:2000]
            out.append(f"{scope}:\n{head}")
        else:
            out.append(f"{scope}: does not exist yet")
    return "\n\n".join(out) or "(empty scope)"


def parse_plan(text: str, fallback_objective: str = "") -> Plan:
    raw = _extract_json(text)
    if raw is None:
        return Plan(
            objective=fallback_objective,
            steps=[],
            unknowns=["planner did not return parseable JSON"],
        ).seal()
    steps = []
    for i, s in enumerate(raw.get("steps", []), start=1):
        steps.append(
            PlanStep(
                id=str(s.get("id", i)),
                intent=s.get("intent", "(no intent given)"),
                action_mode=s.get("action_mode", "direct"),
                tools=list(s.get("tools", [])),
                artifact_scope=list(s.get("artifact_scope", [])),
                max_taint_out=Taint(s.get("max_taint_out", "user")),
                est_tokens=int(s.get("est_tokens", 0) or 0),
                est_seconds=float(s.get("est_seconds", 0) or 0),
                est_cost_usd=float(s.get("est_cost_usd", 0) or 0),
                success_criterion=s.get("success_criterion", "always_pass"),
                reversible=bool(s.get("reversible", True)),
                depends_on=[str(d) for d in s.get("depends_on", [])],
            )
        )
    return Plan(
        objective=raw.get("objective", fallback_objective),
        steps=steps,
        human_approvals_expected=[str(x) for x in raw.get("human_approvals_expected", [])],
        open_questions=list(raw.get("open_questions", [])),
        unknowns=list(raw.get("unknowns", [])),
    ).seal()


def _extract_json(text: str) -> dict | None:
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else None
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def store_plan(ctx, task, plan: Plan, status: str = "drafted") -> Plan:
    plan.seal()
    ctx.conn.execute(
        """INSERT OR REPLACE INTO plans(id, task_id, body_json, status,
                                        approved_hash, created_at)
           VALUES (?,?,?,?,?,?)""",
        (plan.id, task.id, json.dumps(plan.body()), status, None, time.time()),
    )
    ctx.conn.commit()
    write_artifact(
        ctx.conn, task.id, "plan", json.dumps(plan.body(), indent=2), Taint.TRUSTED,
        {"source": "planner"},
    )
    ctx.log.append(
        task.id,
        "plan.drafted",
        {
            "plan_id": plan.id,
            "n_steps": len(plan.steps),
            "est_cost": plan.total_est_cost_usd,
            "est_seconds": plan.total_est_seconds,
        },
    )
    return plan


def load_plan(ctx, task_id: str, status: str | None = None) -> Plan | None:
    q = "SELECT id, body_json, status FROM plans WHERE task_id=?"
    args: tuple = (task_id,)
    if status:
        q += " AND status=?"
        args = (task_id, status)
    q += " ORDER BY created_at DESC LIMIT 1"
    row = ctx.conn.execute(q, args).fetchone()
    if row is None:
        return None
    plan = Plan.model_validate(json.loads(row[1]))
    plan.id = row[0]
    return plan


def approve_plan(ctx, task, plan: Plan, edits: list[dict] | None = None) -> Plan:
    """Human touch #1. Pins the hash to the anchor; execution checks it per step."""
    plan.seal()
    ctx.conn.execute(
        "UPDATE plans SET status='superseded' WHERE task_id=? AND status='approved'",
        (task.id,),
    )
    ctx.conn.execute(
        """INSERT OR REPLACE INTO plans(id, task_id, body_json, status,
                                        approved_hash, created_at)
           VALUES (?,?,?,?,?,?)""",
        (plan.id, task.id, json.dumps(plan.body()), "approved", plan.id, time.time()),
    )
    ctx.conn.commit()
    ctx.log.append(
        task.id,
        "plan.approved",
        {"plan_id": plan.id, "hash": plan.id, "edits": edits or []},
    )
    # Its OWN key. Sharing the task id with `checkpoint()` meant whichever
    # wrote last won, which broke the resume check one way and silently
    # disabled the check below the other way. See Anchor.latest_chain.
    ctx.anchor.record(f"plan:{task.id}", plan.id)
    return plan


def reject_plan(ctx, task, plan: Plan, comment: str) -> None:
    ctx.conn.execute(
        "UPDATE plans SET status='rejected' WHERE id=? AND task_id=?", (plan.id, task.id)
    )
    ctx.conn.commit()
    ctx.log.append(task.id, "plan.rejected", {"plan_id": plan.id, "comment": comment})


def edit_step(plan: Plan, step_id: str, **changes) -> Plan:
    """Human touch #1, verb 2. The plan is re-hashed, so approval is required again."""
    for s in plan.steps:
        if s.id == step_id:
            for k, v in changes.items():
                setattr(s, k, v)
    return plan.seal()


def delete_step(plan: Plan, step_id: str) -> Plan:
    plan.steps = [s for s in plan.steps if s.id != step_id]
    return plan.seal()


def verify_plan_pin(ctx, task) -> Plan:
    """Invariant 17 — verify the approved hash before EVERY step.

    Mutating the plan row after approval, or pointing the task at a different
    plan, halts execution here rather than silently running the new plan.
    """
    row = ctx.conn.execute(
        """SELECT id, body_json, approved_hash FROM plans
           WHERE task_id=? AND status='approved' ORDER BY created_at DESC LIMIT 1""",
        (task.id,),
    ).fetchone()
    if row is None:
        raise IntegrityError("no approved plan for this task")
    plan = Plan.model_validate(json.loads(row[1]))
    plan.id = row[0]
    recomputed = plan.content_hash()
    if recomputed != row[2] or recomputed != row[0]:
        raise IntegrityError(
            f"approved plan was modified after approval "
            f"(stored={row[2][:12]}, recomputed={recomputed[:12]})"
        )
    # Reads the namespaced key, so a chain checkpoint can no longer hide this
    # check by being written after the pin. The legacy form is still accepted
    # for anchor files written before the keys were separated.
    anchored = ctx.anchor.latest(f"plan:{task.id}")
    if anchored is None:
        legacy = ctx.anchor.latest(task.id)
        anchored = legacy[5:] if legacy and legacy.startswith("plan:") else None
    if anchored and anchored != recomputed:
        raise IntegrityError(
            f"approved plan hash does not match the anchor "
            f"(anchor={anchored[:12]}, recomputed={recomputed[:12]})")
    # A3-1: this passes in silence too, so say what it compared. "absent"
    # means the anchor half had nothing to compare against - the half that was
    # inert until D21 - and a record saying so is the only way to tell.
    ctx.log.append(task.id, "plan.pin_verified",
                   {"plan": recomputed[:16],
                    "anchor": "matched" if anchored else "absent"})
    return plan
