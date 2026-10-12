from __future__ import annotations

import json
import time
from contextlib import contextmanager

from ..kernel.tasks import load_task, set_status
from ..kernel.types import TaskStatus
from .shadow import ShadowRepo, ShadowUnavailable

MODES = ("code", "conversation", "both")


class RewindError(RuntimeError):
    pass


def list_checkpoints(ctx, task_id: str) -> list[dict]:
    rows = ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='checkpoint.shadow' ORDER BY seq", (task_id,))
    return [json.loads(r[0]) for r in rows]


@contextmanager
def _exclusive(ctx, task_id: str):
    from ..runlock import RunLockBusy, task_lock

    try:
        lock = task_lock(ctx, task_id).acquire()
    except RunLockBusy:
        raise RewindError("the task is still running a step; wait for it to stop, then rewind") from None
    try:
        yield
    finally:
        lock.release()


def _void_approvals(ctx, task_id: str, step: int) -> None:
    rows = ctx.conn.execute(
        "SELECT id, tool FROM approvals WHERE task_id=? AND consumed_at IS NULL AND status IN ('pending', 'approved') "
        "AND CAST(step_id AS INTEGER) >= ?", (task_id, step)).fetchall()
    for aid, tool in rows:
        ctx.conn.execute("UPDATE approvals SET status='void', consumed_at=? WHERE id=?", (time.time(), aid))
        ctx.log.append(task_id, "approval.consumed",
                       {"approval_id": aid, "tool": tool, "decision": "void", "outcome": "void", "why": "rewound"})
    ctx.conn.commit()


def rewind(ctx, task_id: str, step: int | None = None, what: str = "both") -> dict:
    if what not in MODES:
        raise RewindError(f"what must be one of {', '.join(MODES)}")
    with _exclusive(ctx, task_id):
        return _rewind(ctx, task_id, step, what)


def _rewind(ctx, task_id: str, step: int | None, what: str) -> dict:
    task = load_task(ctx.conn, task_id)
    if task.status == TaskStatus.RUNNING:
        raise RewindError("the task is running; pause it first")
    cps = list_checkpoints(ctx, task_id)
    if not cps:
        raise RewindError("this task has no checkpoints")
    cp = cps[-1] if step is None else next((c for c in cps if c["step"] == step), None)
    if cp is None:
        raise RewindError(f"no checkpoint at step {step}; have {[c['step'] for c in cps]}")

    undo = None
    if what in ("code", "both"):
        if not cp.get("sha"):
            raise RewindError("that checkpoint holds no files (the task has no artifact_scope)")
        repo = ShadowRepo.for_task(ctx, task)
        try:
            undo = repo.checkpoint(f"{task_id} before rewind to step {cp['step']}")
            repo.restore(cp["sha"])
        except ShadowUnavailable as e:
            raise RewindError(str(e)) from e
    if what in ("conversation", "both"):
        ctx.log.append(task_id, "task.park_seed",
                       {k: cp[k] for k in ("artifact", "sha256", "entries")})
    _void_approvals(ctx, task_id, cp["step"])
    ctx.log.append(task_id, "task.rewound", {"step": cp["step"], "what": what, "sha": cp.get("sha"), "undo": undo})
    set_status(ctx.conn, ctx.log, task, TaskStatus.PAUSED, f"rewound to step {cp['step']} ({what})")
    return {"step": cp["step"], "what": what, "undo": undo}


def undo(ctx, task_id: str) -> dict:
    with _exclusive(ctx, task_id):
        return _undo(ctx, task_id)


def _undo(ctx, task_id: str) -> dict:
    last = ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='task.rewound' ORDER BY seq DESC LIMIT 1",
        (task_id,)).fetchone()
    sha = json.loads(last[0]).get("undo") if last else None
    if not sha:
        raise RewindError("nothing to undo")
    try:
        ShadowRepo.for_task(ctx, load_task(ctx.conn, task_id)).restore(sha)
    except ShadowUnavailable as e:
        raise RewindError(str(e)) from e
    ctx.log.append(task_id, "task.rewound", {"step": None, "what": "undo", "sha": sha, "undo": None})
    return {"what": "undo", "sha": sha}
