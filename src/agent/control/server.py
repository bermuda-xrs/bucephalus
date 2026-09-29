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
import os
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..assure.monitor import flags as monitor_flags
from ..context import AgentContext
from ..execute.loop import run_task
from ..instrument.report import run_report
from ..kernel.integrity import verify_all
from ..kernel.tasks import create_task, load_task, set_status
from ..kernel.types import Budget, TaskContract, TaskStatus
from ..plan.planner import approve_plan, draft_plan, load_plan, reject_plan
from ..plan.review import decisions as review_decisions
from ..userdata.store import SuiteTask, ValidationError
from .events import stream_events
from .guard import ALLOW_REMOTE_ENV, AccessGuard, resolve_token

# The console holds a task's event stream open for as long as it shows the
# task, and uvicorn's shutdown waits for open connections with no limit: the
# desktop app took 22 s to close after a task had run, and a stop with the
# console open never finished. Every server here passes this (3.2 / 4.8).
GRACEFUL_SHUTDOWN_S = 1


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


class Decide(BaseModel):
    decision: str  # "approve" | "deny"


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


class DocIn(BaseModel):
    body: str


class NewConversation(BaseModel):
    title: str = ""


class PatchConversation(BaseModel):
    title: str | None = None
    archived: bool | None = None


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
    app = FastAPI(title="alpha test 1", version="0.1.0")
    app.state.ctx = ctx

    # This service can rewrite the reviewer's policy and start runs. Loopback
    # alone is not a boundary when a browser is open — see control/guard.py.
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
        try:
            task = load_task(ctx.conn, task_id)
        except KeyError:
            raise HTTPException(404, "no such task") from None
        plan = load_plan(ctx, task_id)
        step = ctx.conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND type='model.called'",
            (task_id,),
        ).fetchone()[0]
        from .conversations import conversation_of

        answer = ctx.conn.execute(
            "SELECT content FROM artifacts WHERE task_id=? AND kind='answer' "
            "ORDER BY created_at DESC LIMIT 1", (task_id,)).fetchone()
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
            "tokens": tokens,
            "conversation_id": conversation_of(ctx.conn, task_id),
        }

    @app.get("/tasks/{task_id}/stream")
    def stream(task_id: str, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
        since = int(last_event_id) if last_event_id and last_event_id.isdigit() else -1
        # live=True HERE and nowhere else: an SSE client wants the tail, and
        # every in-process caller wants the history and an end.
        return StreamingResponse(
            stream_events(ctx, task_id, since_seq=since, live=True),
            media_type="text/event-stream",
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
    def post_run(task_id: str, background: bool = True) -> dict:
        """Start a run. Returns immediately by default.

        A local turn costs tens of seconds, so a synchronous run leaves the
        caller holding an open request for minutes with nothing to show. The
        console starts it and polls `GET /tasks/{id}` instead.

        `?background=false` keeps the blocking behaviour for scripts that
        want the answer in the response.
        """
        task = load_task(ctx.conn, task_id)
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
                except Exception as e:  # noqa: BLE001 - surfaced via task status
                    ctx.log.append(task_id, "task.status_changed",
                                   {"status": "failed", "reason": f"{type(e).__name__}: {e}"})

            th = threading.Thread(target=_work, name=f"run-{task_id}", daemon=True)
            _runs[task_id] = th
            th.start()
        return {"task_id": task_id, "status": "running", "background": True}

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
        """Integrity breaks, monitor findings, and dropped channel messages.

        NOT approvals, and three separate keys rather than one list with a
        severity field. A broken chain, a monitor finding and a stranger
        messaging the bot mean three different things, and a client that
        received them merged would have to un-merge them to render them
        correctly — which is how they end up rendered alike.
        """
        from ..channel.telegram import recent_drops

        return {
            "integrity": verify_all(ctx.conn, ctx.log, ctx.anchor, ctx.memory),
            "monitor": monitor_flags(ctx.conn),
            "channel_drops": recent_drops(ctx.conn, limit=25),
        }

    @app.get("/metrics/{task_id}")
    def get_metrics(task_id: str) -> dict:
        return run_report(ctx.conn, ctx.telemetry, task_id)

    @app.get("/reviews/{task_id}")
    def get_reviews(task_id: str) -> dict:
        return {"decisions": review_decisions(ctx.conn, task_id)}

    @app.get("/tasks")
    def list_tasks(limit: int = 60) -> dict:
        """The console's queue: newest first, with each task's plan state, so
        the list can be sorted by what needs a human rather than by time."""
        rows = ctx.conn.execute(
            """SELECT t.id, t.contract_json, t.status, t.created_at,
                      (SELECT p.status FROM plans p WHERE p.task_id = t.id
                       ORDER BY p.created_at DESC LIMIT 1)
               FROM tasks t WHERE t.parent_id IS NULL
               ORDER BY t.created_at DESC LIMIT ?""", (max(1, min(limit, 500)),)
        ).fetchall()
        return {"tasks": [{"task_id": r[0], "goal": json.loads(r[1]).get("goal", ""),
                           "status": r[2], "created_at": r[3], "plan_status": r[4]}
                          for r in rows]}

    @app.get("/tasks/{task_id}/plan")
    def get_plan(task_id: str) -> dict:
        row = ctx.conn.execute(
            "SELECT status FROM plans WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
            (task_id,)).fetchone()
        plan = load_plan(ctx, task_id)
        if plan is None or row is None:
            raise HTTPException(404, "no plan drafted for this task")
        return {"plan_id": plan.id, "status": row[0], "plan": plan.body(),
                "rendered": plan.render()}

    @app.post("/tasks/{task_id}/plan/reject")
    def post_reject(task_id: str, body: RejectPlan) -> dict:
        """Send a plan back. The comment goes on the chain beside the plan it
        answers; draft again to get a new one."""
        task = load_task(ctx.conn, task_id)
        plan = load_plan(ctx, task_id)
        if plan is None or plan.id != body.plan_id:
            raise HTTPException(409, "plan_id does not match the current draft")
        reject_plan(ctx, task, plan, body.comment)
        return {"plan_id": plan.id, "status": "rejected"}

    # Conversations (iteration 3, 4.2): threads spanning tasks. A message is
    # a new task in the thread; the console drafts its plan next.
    @app.get("/conversations")
    def get_conversations(q: str = "", archived: bool = False) -> dict:
        from . import conversations as conv

        return {"conversations": conv.listing(ctx.conn, q, archived)}

    @app.post("/conversations")
    def post_conversation(body: NewConversation) -> dict:
        from . import conversations as conv

        return conv.create(ctx.conn, body.title)

    @app.patch("/conversations/{cid}")
    def patch_conversation(cid: str, body: PatchConversation) -> dict:
        from . import conversations as conv

        try:
            return conv.update(ctx.conn, cid, body.title, body.archived)
        except KeyError:
            raise HTTPException(404, "no such conversation") from None

    @app.get("/conversations/{cid}")
    def get_conversation(cid: str) -> dict:
        from . import conversations as conv

        try:
            return conv.thread(ctx, cid)
        except KeyError:
            raise HTTPException(404, "no such conversation") from None

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

    # The context ring and the Control Center's panes (iteration 3, 4.2-4.3).
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

    @app.get("/panels/{name}")
    def get_panel(name: str) -> dict:
        from . import panels

        fn = {"schedules": panels.schedules, "channels": panels.channels,
              "integrity": panels.integrity, "measurement": panels.measurement,
              "settings": panels.settings, "logs": panels.logs}.get(name)
        if fn is None:
            raise HTTPException(404, "no such panel")
        return fn(ctx)

    # The file browser and drag-and-drop (iteration 3, 4.4): a reader and a
    # writer, through the same scope and protected-path checks as every
    # other (control/files.py).
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

    @app.get("/theme")
    def get_theme() -> dict:
        """Colours and banner from themes/ (M13.7). Display only: this is the
        one route by which the banner leaves the package, and it goes to a
        page, never a prompt (agent.tui.theme)."""
        from ..tui.theme import banner_text, load

        pkg = Path(__file__).resolve().parents[1]
        t = load(pkg / "themes" / "default.toml", pkg)
        # A browser renders the glyphs whatever the terminal could.
        return {"colors": t["colors"], "banner": banner_text(t, pkg, glyphs=True)}

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "backends": sorted(ctx.backends),
            "router": ctx.router.health(),
        }

    # ------------------------------------------------------------------
    # USER-AUTHORED CONTENT
    #
    # Suites, policy, grounding, and skills are written by a person here
    # rather than by editing files. The suite in particular cannot ship as
    # repo files: it differs per person and it is the denominator of every
    # number the system reports.
    # ------------------------------------------------------------------

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
        ctx.userdata.delete_task(suite_id, task_id)
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
        """Prior versions of the rule book, so a run can be audited later."""
        return {"history": ctx.userdata.document_history("policy", "default")}

    @app.get("/grounding")
    def list_grounding() -> dict:
        return {"documents": ctx.userdata.list_documents("grounding")}

    @app.put("/grounding/{name}")
    def put_grounding(name: str, body: DocIn) -> dict:
        try:
            return ctx.userdata.put_document("grounding", name, body.body)
        except ValidationError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/grounding/{name}")
    def get_grounding(name: str) -> dict:
        doc = ctx.userdata.get_document("grounding", name)
        if doc is None:
            raise HTTPException(404, "no such document")
        return doc

    @app.delete("/grounding/{name}")
    def delete_grounding(name: str) -> dict:
        ctx.userdata.delete_document("grounding", name)
        return {"deleted": name}

    @app.get("/skills")
    def list_skills() -> dict:
        rows = ctx.conn.execute(
            "SELECT id,name,version,status FROM skills ORDER BY name,version"
        ).fetchall()
        return {"skills": [
            {"id": r[0], "name": r[1], "version": r[2], "status": r[3]} for r in rows
        ]}

    @app.post("/skills")
    def add_skill(body: SkillIn) -> dict:
        """Paste a skill. It lands as a candidate; only the gate promotes."""
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

    # The operator console. Served from the same process as the API so there
    # is nothing to build and nothing to deploy separately. It carries NO
    # secret (AUDIT-2026-09 F2): the token used to be injected here, on the
    # argument that a cross-origin page cannot read the response - true, and
    # beside the point, because any LOCAL process could, sandboxed code
    # included. The launcher that owns the token hands it over in the URL
    # fragment (console_url), which is never sent to a server.
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    # The page's script and styles, as files: the CSP allows no inline ones.
    from fastapi.responses import Response

    for name, media in (("ui.js", "text/javascript"), ("ui.css", "text/css")):
        def asset(name=name, media=media) -> Response:
            body = (Path(__file__).parent / name).read_text(encoding="utf-8")
            return Response(body, media_type=media, headers={"Cache-Control": "no-store"})
        app.get(f"/{name}", include_in_schema=False)(asset)

    return app


def console_url(port: int, token: str, host: str = "127.0.0.1") -> str:
    """How the console is opened: the token in the FRAGMENT. A fragment never
    leaves the browser, so it is in no request, no server log and no proxy."""
    return f"http://{host}:{port}/#token={token}"


# Background runs, keyed by task. A local run takes minutes; the console
# starts one and polls rather than holding a request open.
_runs: dict[str, threading.Thread] = {}
_runs_lock = threading.Lock()


def serve(config_path: str = "config.yaml", host: str = "127.0.0.1", port: int = 8901) -> None:
    import uvicorn

    ctx = AgentContext.open(config_path)
    uvicorn.run(create_app(ctx), host=host, port=port,
                timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S)
