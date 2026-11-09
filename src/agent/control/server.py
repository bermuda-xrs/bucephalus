from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from typing import Literal

from pydantic import BaseModel, Field

from ..assure.monitor import flags as monitor_flags
from ..context import AgentContext
from ..execute.loop import run_task
from ..instrument.report import run_report
from ..kernel.tasks import create_task, load_task, set_status
from ..kernel.types import Budget, TaskContract, TaskStatus
from ..plan.planner import approve_plan, draft_plan, load_plan, reject_plan
from ..plan.review import decisions as review_decisions
from ..userdata.store import SuiteTask, ValidationError
from .events import stream_events
from .guard import ALLOW_REMOTE_ENV, AccessGuard, resolve_token

GRACEFUL_SHUTDOWN_S = 1
# what the trace pane may show: an allowlist, so the monitor's own events never reach the page
TRACE_EVENTS = ("model.called", "model.returned", "model.thought", "tool.intent", "tool.receipt", "review.requested",
                "review.decided", "verify.passed",
                "verify.failed", "read_first.held", "edit_first.held",
                "compute_first.held", "pages.unread_held", "verify.continued", "stuck.warned",
                "stuck.detected", "hook.stop_blocked", "evaluator.returned", "human.message_delivered",
                "human.approved", "human.denied", "tool.not_exposed", "hook.denied", "delegation.opened", "task.status_changed")


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


class RejectPlan(BaseModel):
    plan_id: str
    comment: str = ""


class Steer(BaseModel):
    correction: str


class Say(BaseModel):
    text: str


class Rewind(BaseModel):
    step: int | None = None
    what: str = "both"


class Decide(BaseModel):
    decision: str


class NewSuite(BaseModel):
    name: str
    description: str = ""


class TaskIn(BaseModel):
    key: str
    goal: str
    success_criterion: str = "expected_value"
    output_schema: dict = Field(default_factory=dict)
    artifact_scope: list[str] = Field(default_factory=lambda: ["work"])
    fixtures: dict[str, str] = Field(default_factory=dict)
    budget_usd: float | None = None
    budget_seconds: int | None = None
    catastrophic: bool = False


class ImportIn(BaseModel):
    folder: str = "bench/tasks"


class PolicyIn(BaseModel):
    body: str


class NewConversation(BaseModel):
    title: str = ""


class PatchConversation(BaseModel):
    title: str | None = None
    archived: bool | None = None
    pinned: bool | None = None


class LongFilesIn(BaseModel):
    mode: Literal["pages", "elide", "whole"]


class WebhookIn(BaseModel):
    name: str
    goal: str
    scope: str = "work"


class EnabledIn(BaseModel):
    enabled: bool


class ConfigProposeIn(BaseModel):
    changes: dict


class ConfigApplyIn(BaseModel):
    hash: str
    phrase: str
    acks: list[str] = []


class AllowIn(BaseModel):
    sender: str
    confirm: str


class KeyIn(BaseModel):
    value: str


class ModelIn(BaseModel):
    model: str
    slot: str = "cloud"


class Forget(BaseModel):
    text: str


class NewSchedule(BaseModel):
    spec: str
    goal: str
    scope: str = "work"
    budget_usd: float | None = None


class ArchiveOlder(BaseModel):
    days: int = Field(ge=1, le=3650)
    dry_run: bool = False


class PathIn(BaseModel):
    path: str


class Message(BaseModel):
    text: str
    artifact_scope: list[str] = Field(default_factory=lambda: ["work"])
    budget_usd: float | None = None
    budget_seconds: int | None = None


class Upload(BaseModel):
    path: str
    content_b64: str


class SkillIn(BaseModel):
    name: str
    body: str
    applies_when: str = ""


def create_app(ctx: AgentContext) -> FastAPI:
    app = FastAPI(title="Bucephalus", version="0.1.0")
    app.state.ctx = ctx

    token = resolve_token(ctx.config.root)
    allow_remote = os.environ.get(ALLOW_REMOTE_ENV, "") == "1"
    app.state.token = token
    app.add_middleware(AccessGuard, token=token, allow_remote=allow_remote)

    def _task(task_id: str):
        try:
            return load_task(ctx.conn, task_id)
        except KeyError:
            raise HTTPException(404, "no such task") from None

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
        out = {"task_id": task.id, "status": task.status.value}
        if not contract.artifact_scope:
            out["warning"] = ("no artifact_scope: this task may read or write no "
                              "file (an empty scope permits nothing)")
        return out

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        from .conversations import shown_answer

        task = _task(task_id)
        plan = load_plan(ctx, task_id)
        step = ctx.conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND type='model.called'",
            (task_id,),
        ).fetchone()[0]
        from .conversations import conversation_of

        answer = ctx.conn.execute(
            "SELECT content FROM artifacts WHERE task_id=? AND kind='answer' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (task_id,)).fetchone()
        tokens = ctx.conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0) "
            "FROM calls WHERE task_id=?", (task_id,)).fetchone()[0]
        return {
            "task_id": task.id,
            "status": task.status.value,
            "goal": task.contract.goal,
            "budget": json.loads(task.budget.model_dump_json()),
            "current_step": step,
            "plan_id": plan.id if plan else None,
            "answer": answer[0] if answer else None,
            "answer_shown": shown_answer(answer[0]) if answer else None,
            "tokens": tokens,
            "conversation_id": conversation_of(ctx.conn, task_id),
        }

    @app.get("/tasks/{task_id}/stream")
    def stream(task_id: str, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
        _task(task_id)
        since = int(last_event_id) if last_event_id and last_event_id.isdigit() else -1
        return StreamingResponse(
            stream_events(ctx, task_id, since_seq=since, live=True),
            media_type="text/event-stream",
        )

    @app.post("/tasks/{task_id}/plan")
    def post_plan(task_id: str) -> dict:
        task = _task(task_id)
        plan = draft_plan(ctx, task)
        return {"plan_id": plan.id, "plan": plan.body(), "rendered": plan.render()}

    @app.post("/tasks/{task_id}/plan/approve")
    def post_approve(task_id: str, body: ApprovePlan) -> dict:
        task = _task(task_id)
        plan = load_plan(ctx, task_id)
        if plan is None or plan.id != body.plan_id:
            raise HTTPException(409, "plan_id does not match the current draft")
        approve_plan(ctx, task, plan, body.edits)
        return {"plan_id": plan.id, "status": "approved"}

    @app.post("/tasks/{task_id}/run")
    def post_run(task_id: str, background: bool = True) -> dict:
        task = _task(task_id)
        if ctx.config.autonomy.plan_required and ctx.approved_plan(task) is None:
            raise HTTPException(409, "autonomy.plan_required: approve a plan first")

        if not background:
            result = run_task(ctx, task_id)
            return {
                "task_id": result.task_id, "status": result.status,
                "answer": result.answer, "steps": result.steps,
                "approval_id": result.approval_id,
            }

        with _runs_lock:
            if _runs.get(task_id) and _runs[task_id].is_alive():
                raise HTTPException(409, "this task is already running")

            def _work() -> None:
                try:
                    run_task(ctx, task_id)
                except Exception as e:  # noqa: BLE001
                    ctx.log.append(task_id, "task.status_changed",
                                   {"status": "failed", "reason": f"{type(e).__name__}: {e}"})

            th = threading.Thread(target=_work, name=f"run-{task_id}", daemon=True)
            _runs[task_id] = th
            th.start()
        return {"task_id": task_id, "status": "running", "background": True}

    @app.post("/tasks/{task_id}/steer")
    def post_steer(task_id: str, body: Steer) -> dict:
        from ..kernel.tasks import steer

        _task(task_id)
        child = steer(ctx.conn, ctx.log, task_id, body.correction)
        return {"child_task_id": child.id, "parent_task_id": task_id}

    @app.post("/tasks/{task_id}/message")
    def post_task_message(task_id: str, body: Say) -> dict:
        from ..execute.say import SayError, say

        _task(task_id)
        try:
            return say(ctx, task_id, body.text)
        except SayError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/tasks/{task_id}/pause")
    def post_pause(task_id: str) -> dict:
        task = _task(task_id)
        set_status(ctx.conn, ctx.log, task, TaskStatus.PAUSED, "human requested")
        return {"task_id": task_id, "status": "paused"}

    @app.get("/tasks/{task_id}/checkpoints")
    def get_checkpoints(task_id: str) -> list:
        from ..execute.rewind import list_checkpoints

        _task(task_id)
        return [{"step": c["step"], "sha": c["sha"], "entries": c["entries"]} for c in list_checkpoints(ctx, task_id)]

    @app.get("/tasks/{task_id}/calls")
    def get_calls(task_id: str) -> list:
        _task(task_id)
        out, by_key = [], {}
        for etype, payload in ctx.conn.execute(
                "SELECT type, payload_json FROM events WHERE task_id=? AND type IN ('tool.intent', 'tool.receipt') "
                "ORDER BY seq", (task_id,)):
            d = json.loads(payload)
            if etype == "tool.intent":
                row = {"key": d.get("key", ""), "tool": d.get("tool"), "preview": d.get("preview", "")}
                out.append(row)
                by_key[row["key"]] = row
            elif d.get("key") in by_key:
                by_key[d["key"]].update(ok=bool(d.get("ok")), ms=d.get("wall_ms"))
        return out

    @app.get("/tasks/{task_id}/trace")
    def get_trace(task_id: str) -> list:
        _task(task_id)
        rows = ctx.conn.execute(f"SELECT seq, type, payload_json FROM events WHERE task_id=? AND type IN "
                                f"({','.join('?' * len(TRACE_EVENTS))}) ORDER BY seq", (task_id, *TRACE_EVENTS))
        return [{"seq": seq, "type": t, "data": json.loads(p)} for seq, t, p in rows]

    @app.get("/tasks/{task_id}/call")
    def get_call(task_id: str, key: str) -> dict:
        from ..ledger.artifacts import read_artifact

        _task(task_id)
        row = ctx.conn.execute(
            "SELECT json_extract(payload_json, '$.id') FROM events WHERE task_id=? AND type='artifact.written' "
            "AND json_extract(payload_json, '$.key')=? ORDER BY seq LIMIT 1", (task_id, key)).fetchone()
        art = read_artifact(ctx.conn, row[0]) if row else None
        if art is None:
            raise HTTPException(404, "no such call")
        rec = ctx.conn.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND type='tool.receipt' "
            "AND json_extract(payload_json, '$.key')=? ORDER BY seq LIMIT 1", (task_id, key)).fetchone()
        receipt = json.loads(rec[0]) if rec else {}
        origin = json.loads(ctx.conn.execute("SELECT origin_json FROM artifacts WHERE id=?", (art["id"],)).fetchone()[0])
        text = art["content"]
        return {"tool": origin.get("tool"), "args": origin.get("args", {}), "result": text[:6000],
                "truncated": len(text) > 6000, "taint": art["taint"].value, "ok": receipt.get("ok"),
                "wall_ms": receipt.get("wall_ms")}

    @app.get("/tasks/{task_id}/diff")
    def get_diff(task_id: str, step: int | None = None) -> list:
        from ..execute.diffview import task_diffs

        _task(task_id)
        return task_diffs(ctx, task_id, step)

    @app.post("/tasks/{task_id}/rewind")
    def post_rewind(task_id: str, body: Rewind) -> dict:
        from ..execute.rewind import RewindError, rewind, undo

        _task(task_id)
        with _runs_lock:
            if _runs.get(task_id) and _runs[task_id].is_alive():
                raise HTTPException(409, "the task is running; pause it first")
        try:
            return undo(ctx, task_id) if body.what == "undo" else rewind(ctx, task_id, body.step, body.what)
        except RewindError as e:
            raise HTTPException(409, str(e)) from e

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
        from ..channel.telegram import recent_drops
        from .panels import integrity_findings

        return {
            "integrity": integrity_findings(ctx),
            "monitor": monitor_flags(ctx.conn),
            "channel_drops": recent_drops(ctx.conn, limit=25),
        }

    @app.get("/metrics/{task_id}")
    def get_metrics(task_id: str) -> dict:
        _task(task_id)
        return run_report(ctx.conn, ctx.telemetry, task_id)

    @app.get("/reviews/{task_id}")
    def get_reviews(task_id: str) -> dict:
        _task(task_id)
        return {"decisions": review_decisions(ctx.conn, task_id)}

    @app.get("/tasks")
    def list_tasks(limit: int = 60) -> dict:
        rows = ctx.conn.execute(
            """SELECT t.id, t.contract_json, t.status, t.created_at,
                      (SELECT p.status FROM plans p WHERE p.task_id = t.id
                       ORDER BY p.created_at DESC, p.rowid DESC LIMIT 1)
               FROM tasks t WHERE t.parent_id IS NULL
               ORDER BY t.created_at DESC, t.rowid DESC LIMIT ?""", (max(1, min(limit, 500)),)
        ).fetchall()
        return {"tasks": [{"task_id": r[0], "goal": json.loads(r[1]).get("goal", ""),
                           "status": r[2], "created_at": r[3], "plan_status": r[4]}
                          for r in rows]}

    @app.get("/tasks/{task_id}/plan")
    def get_plan(task_id: str) -> dict:
        _task(task_id)
        row = ctx.conn.execute(
            "SELECT status FROM plans WHERE task_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (task_id,)).fetchone()
        plan = load_plan(ctx, task_id)
        if plan is None or row is None:
            raise HTTPException(404, "no plan drafted for this task")
        return {"plan_id": plan.id, "status": row[0], "plan": plan.body(),
                "rendered": plan.render()}

    @app.post("/tasks/{task_id}/plan/reject")
    def post_reject(task_id: str, body: RejectPlan) -> dict:
        task = _task(task_id)
        plan = load_plan(ctx, task_id)
        if plan is None or plan.id != body.plan_id:
            raise HTTPException(409, "plan_id does not match the current draft")
        reject_plan(ctx, task, plan, body.comment)
        return {"plan_id": plan.id, "status": "rejected"}

    @app.get("/conversations")
    def get_conversations(q: str = "", archived: bool = False) -> dict:
        from . import conversations as conv

        return {"conversations": conv.listing(ctx.conn, q, archived)}

    @app.post("/conversations")
    def post_conversation(body: NewConversation) -> dict:
        from . import conversations as conv

        return conv.create(ctx.conn, body.title)

    @app.post("/conversations/archive_older")
    def post_archive_older(body: ArchiveOlder) -> dict:
        from . import conversations as conv

        return {"count": conv.archive_older(ctx.conn, body.days, body.dry_run)}

    @app.patch("/conversations/{cid}")
    def patch_conversation(cid: str, body: PatchConversation) -> dict:
        from . import conversations as conv

        try:
            return conv.update(ctx.conn, cid, body.title, body.archived, body.pinned)
        except KeyError:
            raise HTTPException(404, "no such conversation") from None

    @app.get("/conversations/{cid}")
    def get_conversation(cid: str) -> dict:
        from . import conversations as conv

        try:
            return conv.thread(ctx, cid)
        except KeyError:
            raise HTTPException(404, "no such conversation") from None

    @app.get("/conversations/{cid}/export")
    def export_conversation(cid: str, format: str = "md"):
        from fastapi.responses import Response

        from . import conversations as conv

        if format not in ("md", "json"):
            raise HTTPException(400, "format is md or json")
        try:
            th = conv.thread(ctx, cid)
        except KeyError:
            raise HTTPException(404, "no such conversation") from None
        body = conv.markdown(th) if format == "md" else json.dumps(th, indent=2)
        kind = "text/markdown; charset=utf-8" if format == "md" else "application/json"
        return Response(body, media_type=kind, headers={
            "Content-Disposition": f'attachment; filename="conversation-{cid}.{format}"'})

    @app.post("/conversations/{cid}/messages")
    def post_message(cid: str, body: Message) -> dict:
        from . import conversations as conv

        if not body.text.strip():
            raise HTTPException(400, "an empty message asks for nothing")
        try:
            return conv.add_message(ctx, cid, body.text.strip(), body.artifact_scope,
                                    body.budget_usd, body.budget_seconds)
        except KeyError:
            raise HTTPException(404, "no such conversation") from None

    @app.get("/context")
    def get_context(task_id: str | None = None) -> dict:
        from .panels import context_meter

        return context_meter(ctx, task_id)

    @app.get("/memory")
    def get_memory() -> dict:
        from .panels import memory

        return memory(ctx)

    @app.post("/memory/accept")
    def post_memory_accept() -> dict:
        from .panels import accept_memory

        return accept_memory(ctx)

    def _local(body: ModelIn):
        from ..models import local

        b = ctx.backends.get(body.slot)
        server = b and local.server_of(b.cfg)
        if not server:
            raise HTTPException(400, f"{body.slot} is not served by a local model server (model_server)")
        try:
            return local, b, local.guard(server, [x.cfg for x in ctx.backends.values()])
        except local.Refused as e:
            raise HTTPException(403, str(e)) from None

    @app.get("/models")
    def get_models() -> dict:
        from ..models import local

        b = ctx.backends.get("cloud")
        servers = [local.status(e) for e in local.endpoints([x.cfg for x in ctx.backends.values()])]
        executor = b and {"slot": "cloud", "model": b.cfg.model, "endpoint": local.server_of(b.cfg),
                          "local": local.server_of(b.cfg) is not None}
        srv = executor and next((s for s in servers if s["endpoint"] == executor["endpoint"]), None)
        if srv:
            executor.update(local.window_warning(srv["loaded"], b.cfg.model, b.cfg.budgeted_window) or {})
        rname = ctx.config.autonomy.auto_review_backend
        r = ctx.backends.get(rname) if rname != "cloud" else None
        reviewer = r and {"slot": rname, "model": r.cfg.model, "endpoint": local.server_of(r.cfg),
                          "local": local.server_of(r.cfg) is not None}
        return {"servers": servers, "executor": executor, "reviewer": reviewer}

    @app.post("/models/run")
    def post_model_run(body: ModelIn) -> dict:
        local, b, endpoint = _local(body)
        try:
            out = local.run(endpoint, body.model, Path(ctx.config.root))
            out.update(local.window_warning([{"name": body.model, "context_length": out["context_length"]}],
                                            body.model, b.cfg.budgeted_window) or {})
            return out
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except local.Refused as e:
            raise HTTPException(503, str(e)) from None
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"the model server failed: {e}") from None

    @app.post("/models/unload")
    def post_model_unload(body: ModelIn) -> dict:
        local, _, endpoint = _local(body)
        try:
            return local.unload(endpoint, body.model)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"the model server failed: {e}") from None

    @app.post("/models/select")
    def post_model_select(body: ModelIn) -> dict:
        local, b, endpoint = _local(body)
        if any(t.is_alive() for t in _runs.values()):
            raise HTTPException(409, "a task is running; switch models when it has finished")
        if body.model not in local.installed(endpoint):
            raise HTTPException(400, f"{body.model} is not installed on {endpoint}")
        b.cfg.model = body.model
        if hasattr(b, "model"):
            b.model = body.model
        ctx.config.backends[body.slot].model = body.model
        return {"slot": body.slot, "model": body.model}

    @app.post("/settings/long_files")
    def post_long_files(body: LongFilesIn) -> dict:
        if any(t.is_alive() for t in _runs.values()):
            raise HTTPException(409, "a task is running; change this when it has finished")
        ctx.config.long_files = body.mode
        ctx.config.profile_provenance["long_files"] = body.mode
        return {"mode": body.mode}

    @app.post("/memory/forget")
    def post_memory_forget(body: Forget) -> dict:
        from ..memory.store import forget

        if not body.text.strip():
            raise HTTPException(400, "say what to forget")
        return {"removed": forget(ctx.memory, body.text)}

    @app.post("/schedules")
    def post_schedule(body: NewSchedule) -> dict:
        from .. import schedule as sched

        lim = ctx.config.limits
        try:
            sid = sched.add(ctx.conn, body.spec,
                            TaskContract(goal=body.goal, artifact_scope=[body.scope or "work"]),
                            Budget(usd=body.budget_usd or lim.default_task_budget_usd,
                                   seconds=lim.default_task_budget_seconds), anchor=ctx.anchor)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {"id": sid}

    @app.put("/schedules/{sid}")
    def put_schedule(sid: str, body: NewSchedule) -> dict:
        from .. import schedule as sched

        lim = ctx.config.limits
        try:
            new = sched.replace(ctx.conn, sid, body.spec,
                                TaskContract(goal=body.goal, artifact_scope=[body.scope or "work"]),
                                Budget(usd=body.budget_usd or lim.default_task_budget_usd,
                                       seconds=lim.default_task_budget_seconds), anchor=ctx.anchor)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if new is None:
            raise HTTPException(404, "no such active schedule")
        return {"id": new, "replaced": sid}

    @app.post("/schedules/{sid}/pause")
    def pause_schedule(sid: str) -> dict:
        from .. import schedule as sched

        if not any(s["id"] == sid for s in sched.active(ctx.conn)):
            raise HTTPException(404, "no such active schedule")
        if not sched.pause(ctx.conn, sid):
            raise HTTPException(409, "already paused")
        return {"id": sid, "paused": True}

    @app.post("/schedules/{sid}/resume")
    def resume_schedule(sid: str) -> dict:
        from .. import schedule as sched

        if not any(s["id"] == sid for s in sched.active(ctx.conn)):
            raise HTTPException(404, "no such active schedule")
        if not sched.resume(ctx.conn, sid):
            raise HTTPException(409, "not paused")
        return {"id": sid, "paused": False}

    @app.post("/schedules/{sid}/run")
    def run_schedule(sid: str) -> dict:
        from .. import schedule as sched

        try:
            out = sched.run_now(ctx, sid)
        except LookupError:
            raise HTTPException(404, "no such active schedule") from None
        except sched.Busy as e:
            raise HTTPException(409, str(e)) from None
        if out["action"] == "tampered":
            raise HTTPException(409, "this schedule changed after it was created, so it will not run; see Integrity")
        return out

    @app.delete("/schedules/{sid}")
    def delete_schedule(sid: str) -> dict:
        from .. import schedule as sched

        if not sched.remove(ctx.conn, sid):
            raise HTTPException(404, "no such active schedule")
        return {"removed": sid}

    @app.get("/mcp")
    def get_mcp() -> dict:
        from . import mcp_page

        return mcp_page.listing(ctx)

    @app.post("/mcp/{name}/test")
    def post_mcp_test(name: str) -> dict:
        from . import mcp_page

        if name not in ctx.config.mcp_servers:
            raise HTTPException(404, "no such configured server")
        busy = ctx.conn.execute("SELECT 1 FROM tasks WHERE status='running' LIMIT 1").fetchone()
        if busy or any(t.is_alive() for t in _runs.values()):
            raise HTTPException(409, "a task is running; test a server when it has finished")
        return mcp_page.test(ctx, name)

    proposals: dict = {}

    def _config_call(fn, *a):
        from . import config_page

        try:
            return fn(*a)
        except config_page.Refused as e:
            raise HTTPException(e.status, str(e)) from None

    def _hook_call(fn, *a):
        from . import webhooks

        try:
            return fn(*a)
        except webhooks.Refused as e:
            raise HTTPException(e.status, str(e), headers=e.headers or None) from None

    @app.post("/hooks/{name}", status_code=202)
    async def post_hook(name: str, request: Request) -> dict:
        from starlette.concurrency import run_in_threadpool

        from . import webhooks

        secret = request.headers.get("x-hook-secret", "")
        await run_in_threadpool(_hook_call, webhooks.authenticate, ctx, name, secret)
        declared = request.headers.get("content-length", "0")
        if not declared.isdigit() or int(declared) > webhooks.MAX_BODY:
            raise HTTPException(413, "too large")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > webhooks.MAX_BODY:
                raise HTTPException(413, "too large")
        return await run_in_threadpool(_hook_call, webhooks.receive, ctx, name, secret,
                                       request.headers.get("x-delivery-id", ""), body)

    @app.get("/webhooks")
    def get_webhooks() -> dict:
        from . import webhooks

        return {"hooks": webhooks.listing(ctx)}

    @app.post("/webhooks")
    def post_webhook(body: WebhookIn) -> dict:
        from . import webhooks

        return _hook_call(webhooks.create, ctx, body.name, body.goal, body.scope)

    @app.post("/webhooks/{name}/enabled")
    def post_webhook_enabled(name: str, body: EnabledIn) -> dict:
        from . import webhooks

        return _hook_call(webhooks.set_enabled, ctx, name, body.enabled)

    @app.delete("/webhooks/{name}")
    def delete_webhook(name: str) -> dict:
        from . import webhooks

        return _hook_call(webhooks.delete, ctx, name)

    @app.get("/config")
    def get_config() -> dict:
        from . import config_page

        return _config_call(config_page.view, ctx)

    @app.post("/config/propose")
    def post_config_propose(body: ConfigProposeIn) -> dict:
        from . import config_page

        return _config_call(config_page.propose, ctx, proposals, body.changes)

    @app.post("/config/apply")
    def post_config_apply(body: ConfigApplyIn) -> dict:
        from . import config_page

        return _config_call(config_page.apply, ctx, proposals, body.hash, body.phrase, body.acks)

    @app.post("/channels/telegram/allow")
    def post_allow(body: AllowIn) -> dict:
        from ..channel.telegram import allow_sender

        if not (body.sender.isascii() and body.sender.isdigit() and len(body.sender) <= 20):
            raise HTTPException(400, "a Telegram sender is a number")
        if body.confirm != body.sender:
            raise HTTPException(400, "type the sender id again to confirm")
        allow_sender(ctx.conn, body.sender)
        return {"sender": body.sender, "source": "console"}

    @app.delete("/channels/telegram/allow/{sender}")
    def delete_allow(sender: str) -> dict:
        from ..channel.telegram import revoke_sender

        if revoke_sender(ctx.conn, sender):
            return {"sender": sender, "removed": True}
        if sender in {str(x) for x in ctx.config.channels.telegram.allowed_senders}:
            raise HTTPException(409, "this sender is in your config; edit the config to remove it")
        raise HTTPException(404, "no such sender")

    @app.get("/keys")
    def get_keys() -> dict:
        from .. import keystore

        return {"keys": [{"name": n, "used_by": u, "set": keystore.source(n) is not None, "source": keystore.source(n)}
                         for n, u in sorted(keystore.declared(ctx.config).items())]}

    def _declared_key(name: str) -> None:
        from .. import keystore

        if name not in keystore.declared(ctx.config):
            raise HTTPException(404, f"{name} is not a key the config declares")

    @app.put("/keys/{name}")
    def put_key(name: str, body: KeyIn) -> dict:
        from .. import keystore

        _declared_key(name)
        from ..profiles import LOCAL, PROVIDER_CREDENTIALS

        if ctx.config.profile == LOCAL and name in PROVIDER_CREDENTIALS:
            raise HTTPException(409, "profile=local refuses provider credentials; run with --profile mixed to use one")
        try:
            keystore.put(name, body.value)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except keystore.SyncedFolder as e:
            raise HTTPException(409, str(e)) from None
        return {"name": name, "set": True}

    @app.delete("/keys/{name}")
    def delete_key(name: str) -> dict:
        from .. import keystore

        _declared_key(name)
        try:
            keystore.remove(name)
        except keystore.SyncedFolder as e:
            raise HTTPException(409, str(e)) from None
        return {"name": name, "set": False}

    @app.get("/system")
    def get_system() -> dict:
        from . import system

        return system.info(ctx)

    @app.post("/system/backup")
    def post_backup() -> dict:
        from . import system

        try:
            return system.backup(ctx)
        except system.SyncedFolder as e:
            raise HTTPException(409, str(e)) from None

    @app.get("/system/backups")
    def get_backups() -> dict:
        from . import system

        return {"backups": system.listing()}

    @app.get("/system/backups/{name}")
    def get_backup(name: str):
        from . import system

        p = system.find(name)
        if p is None:
            raise HTTPException(404, "no such backup")
        return FileResponse(p, media_type="application/zip", filename=name)

    @app.get("/documents")
    def get_documents() -> dict:
        from . import docs

        root = docs.source_root()
        return {"available": root is not None, "docs": docs.listing(root)}

    @app.get("/documents/{name}")
    def get_document(name: str) -> dict:
        from . import docs

        p = docs.files(docs.source_root()).get(name)
        if p is None:
            raise HTTPException(404, "no such document")
        return {"name": name, "kind": docs.kind(name), "text": p.read_text(encoding="utf-8", errors="replace")}

    @app.get("/analytics")
    def get_analytics(days: int = 30) -> dict:
        from .analytics import usage

        if days not in (7, 30, 90):
            raise HTTPException(422, "days must be 7, 30 or 90")
        return usage(ctx.conn, days)

    @app.get("/panels/{name}")
    def get_panel(name: str) -> dict:
        from . import panels

        fn = {"schedules": panels.schedules, "channels": panels.channels,
              "integrity": panels.integrity, "measurement": panels.measurement,
              "settings": panels.settings, "logs": panels.logs}.get(name)
        if fn is None:
            raise HTTPException(404, "no such panel")
        return fn(ctx)

    @app.get("/tasks/{task_id}/files")
    def get_files(task_id: str, path: str = "") -> dict:
        from . import files

        task = _task(task_id)
        try:
            return files.browse(ctx, task, path or (task.contract.artifact_scope or [""])[0])
        except files.Refused as e:
            raise HTTPException(403, str(e)) from None
        except files.Missing:
            raise HTTPException(404, "no such file in this task's scope") from None
        except files.Unwritable as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/tasks/{task_id}/files")
    def post_files(task_id: str, body: Upload) -> dict:
        from . import files

        task = _task(task_id)
        try:
            return files.drop(ctx, task, body.path, body.content_b64)
        except files.Refused as e:
            raise HTTPException(403, str(e)) from None
        except files.Exists as e:
            raise HTTPException(409, str(e)) from None
        except files.Unwritable as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/tasks/{task_id}/files/mkdir")
    def post_folder(task_id: str, body: PathIn) -> dict:
        from . import files

        try:
            return files.make_folder(ctx, _task(task_id), body.path)
        except files.Refused as e:
            raise HTTPException(403, str(e)) from None
        except files.Exists as e:
            raise HTTPException(409, str(e)) from None
        except files.Unwritable as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/tasks/{task_id}/files")
    def delete_file(task_id: str, path: str) -> dict:
        from . import files

        try:
            return files.remove(ctx, _task(task_id), path)
        except files.Refused as e:
            raise HTTPException(403, str(e)) from None
        except files.Missing as e:
            raise HTTPException(404, f"nothing at {e}") from None
        except files.Busy as e:
            raise HTTPException(409, str(e)) from None

    @app.get("/theme")
    def get_theme() -> dict:
        from ..tui.theme import banner_text, load

        pkg = Path(__file__).resolve().parents[1]
        t = load(pkg / "themes" / "default.toml", pkg)
        return {"colors": t["colors"], "banner": banner_text(t, pkg, glyphs=True)}

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "backends": sorted(ctx.backends),
        }

    def _verifiers() -> set[str]:
        return set(ctx.mesh.registry)

    @app.get("/suites")
    def list_suites() -> dict:
        return {"suites": ctx.userdata.list_suites(),
                "min_paired_trials": ctx.config.gate.min_paired_trials}

    @app.post("/suites")
    def create_suite(body: NewSuite) -> dict:
        try:
            s = ctx.userdata.create_suite(body.name, body.description)
        except ValidationError as e:
            raise HTTPException(400, str(e)) from None
        return {"id": s.id, "name": s.name}

    @app.get("/suites/{suite_id}")
    def get_suite(suite_id: str) -> dict:
        s = ctx.userdata.get_suite(suite_id)
        if s is None:
            raise HTTPException(404, "no such suite")
        return {
            "id": s.id, "name": s.name, "description": s.description,
            "n_tasks": len(s.tasks),
            "min_paired_trials": ctx.config.gate.min_paired_trials,
            "verifiers": sorted(_verifiers()),
            "tasks": [
                {"id": t.id, "key": t.task_key, "goal": t.goal,
                 "success_criterion": t.success_criterion,
                 "output_schema": t.output_schema,
                 "artifact_scope": t.artifact_scope, "fixtures": t.fixtures,
                 "budget_usd": t.budget_usd, "budget_seconds": t.budget_seconds,
                 "catastrophic": t.catastrophic}
                for t in s.tasks
            ],
        }

    @app.delete("/suites/{suite_id}")
    def delete_suite(suite_id: str) -> dict:
        if ctx.userdata.get_suite(suite_id) is None:
            raise HTTPException(404, "no such suite")
        ctx.userdata.delete_suite(suite_id)
        return {"deleted": suite_id}

    @app.post("/suites/{suite_id}/tasks")
    def upsert_task(suite_id: str, body: TaskIn) -> dict:
        t = SuiteTask(
            task_key=body.key, goal=body.goal,
            success_criterion=body.success_criterion,
            output_schema=body.output_schema, artifact_scope=body.artifact_scope,
            fixtures=body.fixtures, budget_usd=body.budget_usd,
            budget_seconds=body.budget_seconds, catastrophic=body.catastrophic,
        )
        try:
            saved = ctx.userdata.upsert_task(suite_id, t, _verifiers())
        except ValidationError as e:
            raise HTTPException(400, str(e)) from None
        return {"id": saved.id, "key": saved.task_key}

    @app.delete("/suites/{suite_id}/tasks/{task_id}")
    def delete_task(suite_id: str, task_id: str) -> dict:
        if not ctx.userdata.delete_task(suite_id, task_id):
            raise HTTPException(404, "no such task in this suite")
        return {"deleted": task_id}

    @app.post("/suites/{suite_id}/import")
    def import_tasks(suite_id: str, body: ImportIn) -> dict:
        s = ctx.userdata.get_suite(suite_id)
        if s is None:
            raise HTTPException(404, "no such suite")
        try:
            out = ctx.userdata.import_suite_from_dir(s.name, ctx.config.resolve(body.folder))
        except ValidationError as e:
            raise HTTPException(400, str(e)) from None
        return {"id": out.id, "n_tasks": len(out.tasks)}

    @app.get("/policy")
    def get_policy() -> dict:
        from ..plan.review import load_policy

        doc = ctx.userdata.get_document("policy", "default")
        return {
            "body": doc["body"] if doc else load_policy(ctx),
            "stored": doc is not None,
            "effective_source": "database" if doc else "file or default",
        }

    @app.put("/policy")
    def put_policy(body: PolicyIn) -> dict:
        try:
            ctx.userdata.set_policy(body.body)
        except ValidationError as e:
            raise HTTPException(400, str(e)) from None
        return {"saved": True,
                "history": ctx.userdata.document_history("policy", "default")}

    @app.get("/policy/history")
    def policy_history() -> dict:
        return {"history": ctx.userdata.document_history("policy", "default")}

    @app.get("/skills")
    def list_skills() -> dict:
        rows = ctx.conn.execute(
            "SELECT id,name,version,status FROM skills ORDER BY name,version"
        ).fetchall()
        return {"skills": [
            {"id": r[0], "name": r[1], "version": r[2], "status": r[3]} for r in rows
        ]}

    @app.get("/skills/{skill_id}")
    def get_skill(skill_id: str) -> dict:
        try:
            s = ctx.skills.load(skill_id, verify=False)
        except KeyError:
            raise HTTPException(404, "no such skill") from None
        return {k: s[k] for k in ("id", "name", "version", "body", "status", "contract")}

    @app.post("/skills")
    def add_skill(body: SkillIn) -> dict:
        try:
            sid = ctx.skills.store(
                name=body.name, body=body.body,
                contract={"applies_when": body.applies_when},
                provenance={"drafted_by": "human", "source": "ui"},
                status="candidate",
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {"id": sid, "status": "candidate"}

    @app.get("/gate/{skill_id}")
    def gate_status(skill_id: str) -> dict:
        from ..skills.gate import evaluate_skill

        d = evaluate_skill(ctx, skill_id)
        return {"verdict": d.verdict, "reason": d.reason, "detail": d.detail}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    from fastapi.responses import Response

    for name, media in (("ui.js", "text/javascript"), ("md.js", "text/javascript"),
                        ("ui.css", "text/css")):
        def asset(name=name, media=media) -> Response:
            body = (Path(__file__).parent / name).read_text(encoding="utf-8")
            return Response(body, media_type=media, headers={"Cache-Control": "no-store"})
        app.get(f"/{name}", include_in_schema=False)(asset)

    return app


def console_url(port: int, token: str, host: str = "127.0.0.1") -> str:
    return f"http://{host}:{port}/#token={token}"


_runs: dict[str, threading.Thread] = {}
_runs_lock = threading.Lock()


def serve(config_path: str = "config.yaml", host: str = "127.0.0.1", port: int = 8901) -> None:
    import uvicorn

    ctx = AgentContext.open(config_path)
    uvicorn.run(create_app(ctx), host=host, port=port,
                timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S)
