"""Audit probe: the TUI's `a` (allow once) decides an approval the operator
was never shown. Scratch state only (conftest.py). Driven headless through
Textual's Pilot, the way tests/test_tui_two_touch.py drives it.

tui/app.py `_decide`:
    pending = self.ctx.pending_approvals(self.task_id) or self.ctx.pending_approvals()
    a = pending[0]
    self.ctx.resolve_approval(a["id"], approve)
    ...
    self._run(a["task_id"])

When the task on screen has nothing parked, the fallback takes the oldest
pending approval of ANY task. The approvals tab only ever lists the attached
task's approvals (redraw: pending_approvals(self.task_id)), and the side list
shows "wants <tool>: parked" with no preview - so the preview of what gets
approved is never on screen.
"""

from __future__ import annotations

import asyncio

from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.tui.app import AlphaTUI


def test_FINDING_allow_once_approves_another_tasks_unseen_action(ctx, workspace):
    ctx.config.autonomy.plan_required = True         # the product's setting, not the suite's
    other = create_task(ctx.conn, ctx.log, TaskContract(goal="tidy the reports", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=60))
    shown = create_task(ctx.conn, ctx.log, TaskContract(goal="summarise notes", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=60))
    write = ctx.tools.get("write_file")
    aid = ctx.request_approval(other, write, {"path": "work/report.md", "content": "OVERWRITTEN"},
                               "needs a human", 0)

    seen: dict = {}

    async def go():
        app = AlphaTUI(ctx, task_id=shown.id)          # the operator is looking at `shown`
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.5)
            app.query_one("#trail").focus()            # where ask() leaves focus
            queue = app.query_one("#queue")
            seen["queue_lines"] = [str(line.text) if hasattr(line, "text") else str(line)
                                   for line in queue.lines]
            await pilot.press("a")                     # "allow once"
            await pilot.pause(0.5)
            seen["attached_after"] = app.task_id
            for _ in range(100):                       # _run() resumes it on a thread
                await pilot.pause(0.1)
                if ctx.conn.execute("SELECT status FROM tasks WHERE id=?",
                                    (other.id,)).fetchone()[0] not in ("pending", "running"):
                    break

    asyncio.run(go())
    status = ctx.conn.execute("SELECT status FROM approvals WHERE id=?", (aid,)).fetchone()[0]
    print("approvals tab before the key press:", seen["queue_lines"])
    print("approval of the OTHER task is now:", status, "; TUI attached to other task:",
          seen["attached_after"] == other.id)
    assert not any(aid in line or "OVERWRITTEN" in line for line in seen["queue_lines"])
    assert status == "approved"
    # And it RAN that task, which never had a plan drafted, let alone signed:
    # TUI._run calls run_task directly; the plan_required check lives in the
    # server's /run and the CLI, not in run_task.
    types = [e["type"] for e in ctx.log.events(other.id)]
    ran = [e for e in ctx.log.events(other.id) if e["type"] == "model.called"]
    print("other task: plan events", [t for t in types if t.startswith("plan.")],
          "; model calls", len(ran), "; status",
          ctx.conn.execute("SELECT status FROM tasks WHERE id=?", (other.id,)).fetchone()[0])
    assert not [t for t in types if t.startswith("plan.")]
    assert ran, "expected the TUI to have run the other task"
    written = workspace / "report.md"
    print("the unseen write was carried out:", written.exists() and written.read_text(encoding="utf-8"))
    assert written.read_text(encoding="utf-8") == "OVERWRITTEN"
