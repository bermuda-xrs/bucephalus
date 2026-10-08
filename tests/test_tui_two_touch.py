from __future__ import annotations

import asyncio

from agent.tui.app import AlphaTUI


def _events(ctx, tid):
    return [e["type"] for e in ctx.log.events(tid)]


def _status(ctx, tid):
    return ctx.conn.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0]


def _steps_run(ctx, tid):
    return [e for e in ctx.log.events(tid) if e["type"] == "model.called"
            and e["payload"].get("purpose") == "step"]


async def _until(pilot, cond, seconds=20.0):
    for _ in range(int(seconds / 0.1)):
        await pilot.pause(0.1)
        if cond():
            return True
    return False


def test_submitting_drafts_a_plan_and_runs_nothing_until_it_is_signed(ctx, workspace):
    async def go():
        app = AlphaTUI(ctx)
        async with app.run_test(size=(140, 44)) as pilot:
            app.query_one("#goal").value = "summarise work/notes.md"
            await pilot.press("enter")
            assert await _until(pilot, lambda: app.task_id is not None
                                and "plan.drafted" in _events(ctx, app.task_id)), \
                "submitting did not draft a plan"
            tid = app.task_id
            await pilot.pause(1.0)
            assert _status(ctx, tid) == "pending"
            assert not _steps_run(ctx, tid), "a step ran before anyone signed the plan"

            await pilot.press("s")
            assert await _until(pilot, lambda: _status(ctx, tid) == "done", 30), \
                "signing did not run the task"
            assert "plan.approved" in _events(ctx, tid)
    asyncio.run(go())


def test_sending_a_plan_back_records_why_and_runs_nothing(ctx, workspace):
    async def go():
        app = AlphaTUI(ctx)
        async with app.run_test(size=(140, 44)) as pilot:
            app.query_one("#goal").value = "count the files"
            await pilot.press("enter")
            assert await _until(pilot, lambda: app.task_id is not None
                                and "plan.drafted" in _events(ctx, app.task_id))
            tid = app.task_id
            await pilot.press("b")
            app.query_one("#goal").value = "use a table"
            await pilot.press("enter")
            assert await _until(pilot, lambda: ctx.conn.execute(
                "SELECT status FROM plans WHERE task_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (tid,)).fetchone()[0] == "rejected")
            assert not _steps_run(ctx, tid)
    asyncio.run(go())


def test_the_status_bar_carries_the_model_and_the_context_meter(ctx, workspace):
    async def go():
        app = AlphaTUI(ctx)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            bar = str(app.query_one("#budget").render())
            assert "mock-cloud" in bar and "ctx" in bar and "ceiling" in bar
    asyncio.run(go())


def test_the_input_and_the_status_bar_are_on_screen(ctx, workspace):
    from textual.widgets import Footer

    async def go():
        app = AlphaTUI(ctx)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            screen = app.screen.region
            footer = app.query_one(Footer).region
            for wid in ("#goal", "#budget"):
                r = app.query_one(wid).region
                assert r.height > 0 and screen.contains_region(r), (wid, r, screen)
                assert r.bottom <= footer.y, (wid, "under the key bar", r, footer)
    asyncio.run(go())
