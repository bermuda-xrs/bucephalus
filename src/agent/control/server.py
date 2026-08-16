"""M12.1 — the API.

  POST   /tasks                  create; returns task_id
  GET    /tasks/{id}             status, budget, current step
  GET    /tasks/{id}/stream      SSE; Last-Event-ID -> checkpoint resume
  POST   /tasks/{id}/plan        draft a plan (read-only phase)
  POST   /tasks/{id}/plan/approve
  POST   /tasks/{id}/run         execute an approved plan
  POST   /tasks/{id}/steer       inject correction; FORKS the graph
  POST   /tasks/{id}/pause
  GET    /approvals              pending gate decisions, batched
  POST   /approvals/{id}         approve | deny
  GET    /alerts                 integrity + monitor findings (SEPARATE)
  GET    /metrics/{task_id}

/alerts is deliberately separate from /approvals. An integrity break is not a
routine permission request.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..assure.monitor import flags as monitor_flags
from ..context import AgentContext
from ..execute.loop import run_task
from ..instrument.report import run_report
from ..kernel.integrity import verify_all
from ..kernel.tasks import create_task, load_task, set_status
from ..kernel.types import Budget, TaskContract, TaskStatus
from ..plan.planner import approve_plan, draft_plan, load_plan
from ..plan.review import decisions as review_decisions
from .events import stream_events


class CreateTask(BaseModel):
    goal: str
    success_criterion: str = "always_pass"
    artifact_scope: list[str] = Field(default_factory=list)
    output_schema: dict = Field(default_factory=dict)
    budget_usd: float | None = None
    budget_seconds: int | None = None


class ApprovePlan(BaseModel):
    plan_id: str
    edits: list[dict] = Field(default_factory=list)


class Steer(BaseModel):
    correction: str


class Decide(BaseModel):
    decision: str  # "approve" | "deny"


def create_app(ctx: AgentContext) -> FastAPI:
    app = FastAPI(title="alpha test 1", version="0.1.0")
    app.state.ctx = ctx

    @app.post("/tasks")
    def post_task(body: CreateTask) -> dict:
        contract = TaskContract(
            goal=body.goal,
            success_criterion=body.success_criterion,
            artifact_scope=body.artifact_scope,
            output_schema=body.output_schema,
        )
        budget = Budget(
            usd=body.budget_usd or ctx.config.limits.default_task_budget_usd,
            seconds=body.budget_seconds or ctx.config.limits.default_task_budget_seconds,
        )
        task = create_task(ctx.conn, ctx.log, contract, budget)
        return {"task_id": task.id, "status": task.status.value}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        try:
            task = load_task(ctx.conn, task_id)
        except KeyError:
            raise HTTPException(404, "no such task") from None
        plan = load_plan(ctx, task_id)
        step = ctx.conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND type='model.called'",
            (task_id,),
        ).fetchone()[0]
        return {
            "task_id": task.id,
            "status": task.status.value,
            "goal": task.contract.goal,
            "budget": json.loads(task.budget.model_dump_json()),
            "current_step": step,
            "plan_id": plan.id if plan else None,
        }

    @app.get("/tasks/{task_id}/stream")
    def stream(task_id: str, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
        since = int(last_event_id) if last_event_id and last_event_id.isdigit() else -1
        return StreamingResponse(
            stream_events(ctx, task_id, since_seq=since), media_type="text/event-stream"
        )

    @app.post("/tasks/{task_id}/plan")
    def post_plan(task_id: str) -> dict:
        task = load_task(ctx.conn, task_id)
        plan = draft_plan(ctx, task)
        return {"plan_id": plan.id, "plan": plan.body(), "rendered": plan.render()}

    @app.post("/tasks/{task_id}/plan/approve")
    def post_approve(task_id: str, body: ApprovePlan) -> dict:
        task = load_task(ctx.conn, task_id)
        plan = load_plan(ctx, task_id)
        if plan is None or plan.id != body.plan_id:
            raise HTTPException(409, "plan_id does not match the current draft")
        approve_plan(ctx, task, plan, body.edits)
        return {"plan_id": plan.id, "status": "approved"}

    @app.post("/tasks/{task_id}/run")
    def post_run(task_id: str) -> dict:
        task = load_task(ctx.conn, task_id)
        if ctx.config.autonomy.plan_required and ctx.approved_plan(task) is None:
            raise HTTPException(409, "autonomy.plan_required: approve a plan first")
        result = run_task(ctx, task_id)
        return {
            "task_id": result.task_id,
            "status": result.status,
            "answer": result.answer,
            "steps": result.steps,
            "approval_id": result.approval_id,
        }

    @app.post("/tasks/{task_id}/steer")
    def post_steer(task_id: str, body: Steer) -> dict:
        """M12.3 — never mutate a running task. Fork it."""
        parent = load_task(ctx.conn, task_id)
        ctx.log.append(task_id, "human.steered", {"correction": body.correction})
        child_contract = parent.contract.model_copy(
            update={"goal": parent.contract.goal + f"\n\n[steered] {body.correction}"}
        )
        child = create_task(
            ctx.conn, ctx.log, child_contract, parent.budget.model_copy(), parent_id=task_id
        )
        set_status(ctx.conn, ctx.log, parent, TaskStatus.PAUSED, "superseded by steer")
        return {"child_task_id": child.id, "parent_task_id": task_id}

    @app.post("/tasks/{task_id}/pause")
    def post_pause(task_id: str) -> dict:
        task = load_task(ctx.conn, task_id)
        set_status(ctx.conn, ctx.log, task, TaskStatus.PAUSED, "human requested")
        return {"task_id": task_id, "status": "paused"}

    @app.get("/approvals")
    def get_approvals(task_id: str | None = None) -> dict:
        return {"pending": ctx.pending_approvals(task_id)}

    @app.post("/approvals/{approval_id}")
    def post_approval(approval_id: str, body: Decide) -> dict:
        if body.decision not in ("approve", "deny"):
            raise HTTPException(400, "decision must be approve or deny")
        try:
            return ctx.resolve_approval(approval_id, body.decision == "approve")
        except KeyError:
            raise HTTPException(404, "no such approval") from None

    @app.get("/alerts")
    def get_alerts() -> dict:
        """Integrity breaks and monitor findings. NOT approvals. Never merged."""
        return {
            "integrity": verify_all(ctx.conn, ctx.log, ctx.anchor),
            "monitor": monitor_flags(ctx.conn),
        }

    @app.get("/metrics/{task_id}")
    def get_metrics(task_id: str) -> dict:
        return run_report(ctx.conn, ctx.telemetry, task_id)

    @app.get("/reviews/{task_id}")
    def get_reviews(task_id: str) -> dict:
        return {"decisions": review_decisions(ctx.conn, task_id)}

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "backends": sorted(ctx.backends),
            "router": ctx.router.health(),
        }

    return app


def serve(config_path: str = "config.yaml", host: str = "127.0.0.1", port: int = 8901) -> None:
    import uvicorn

    ctx = AgentContext.open(config_path)
    uvicorn.run(create_app(ctx), host=host, port=port)
