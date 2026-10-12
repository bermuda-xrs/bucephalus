from __future__ import annotations

import json

from ..kernel.tasks import load_task
from .rewind import list_checkpoints
from .shadow import ShadowRepo, ShadowUnavailable

MAX_DIFF_CHARS = 50000


def task_diffs(ctx, task_id: str, step: int | None = None) -> list[dict]:
    task = load_task(ctx.conn, task_id)
    cps = [c for c in list_checkpoints(ctx, task_id) if c.get("sha")]
    if not cps:
        return []
    repo = ShadowRepo.for_task(ctx, task)
    try:
        now = repo.checkpoint(f"{task_id} diff view")
        out = []
        for i, c in enumerate(cps):
            if step is not None and c["step"] != step:
                continue
            after = cps[i + 1]["sha"] if i + 1 < len(cps) else now
            text = repo.diff(c["sha"], after)
            out.append({"step": c["step"], "tools": _calls(ctx, task_id, c["step"]),
                        "files": repo.changed(c["sha"], after), "diff": text[:MAX_DIFF_CHARS],
                        "truncated": len(text) > MAX_DIFF_CHARS})
        return out
    except ShadowUnavailable:
        return []


def _calls(ctx, task_id: str, step: int) -> list[str]:
    rows = ctx.conn.execute("SELECT payload_json FROM events WHERE task_id=? AND type='tool.intent' ORDER BY seq",
                            (task_id,))
    return [d["preview"] for d in (json.loads(r[0]) for r in rows) if d.get("step") == step]
