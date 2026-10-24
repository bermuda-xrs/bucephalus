from __future__ import annotations

import json

from agent.config import BackendConfig
from agent.execute.loop import MAX_PAGE_HOLDS, run_task
from agent.execute.tools import tool_read_file
from agent.kernel.tasks import create_task

FINAL = '<final>{"answer": "ok"}</final>'


def _read(page=None):
    args = {"path": "work/long.md"} if page is None else {"path": "work/long.md", "page": page}
    return '<action tool="read_file">' + json.dumps(args) + "</action>"


def _long(workspace):
    (workspace / "long.md").write_text("".join(f"line {i}: filler words to take up the room\n" for i in range(3000)),
                                       encoding="utf-8")


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


def test_it_is_on_for_the_small_profile_only_unless_declared():
    assert BackendConfig(kind="mock", model="m").requires_all_pages is False
    assert BackendConfig(kind="mock", model="m", tool_profile="small").requires_all_pages is True
    assert BackendConfig(kind="mock", model="m", tool_profile="small", read_all_pages=False).requires_all_pages is False
    assert BackendConfig(kind="mock", model="m", read_all_pages=True).requires_all_pages is True


def test_an_answer_with_unread_pages_becomes_a_continuation_naming_them(small, task, workspace):
    _long(workspace)
    pages = tool_read_file(small, task, "work/long.md")["pages"]
    small.config.limits.max_steps = pages + 10
    t = create_task(small.conn, small.log, task.contract, task.budget.model_copy(update={"usd": 10.0}))
    script = [_read(), FINAL] + [_read(n) for n in range(2, pages + 1)] + [FINAL]
    small.backends["cloud"].queue(*script)
    assert run_task(small, t.id).status.startswith("done")
    calls = small.backends["cloud"].calls
    assert "pages " + ", ".join(str(n) for n in range(2, pages + 1)) + " of work/long.md" in calls[2]["tail"]
    assert len(calls) == len(script)
    held = _events(small, t, "pages.unread_held")
    assert len(held) == 1 and list(held[0]["unread"].values()) == [list(range(2, pages + 1))]


def test_after_three_reminders_the_answer_is_taken(small, task, workspace):
    _long(workspace)
    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue(_read(), *[FINAL] * (MAX_PAGE_HOLDS + 1))
    assert run_task(small, t.id).status.startswith("done")
    assert MAX_PAGE_HOLDS == 3 and len(_events(small, t, "pages.unread_held")) == 3
    assert len(small.backends["cloud"].calls) == 2 + MAX_PAGE_HOLDS


def test_the_full_profile_answers_at_once(ctx, task, workspace):
    _long(workspace)
    ctx.backends["cloud"].queue(_read(), FINAL)
    assert run_task(ctx, task.id).status.startswith("done")
    assert _events(ctx, task, "pages.unread_held") == []


def test_without_pages_there_is_nothing_to_hold(small, task, workspace):
    _long(workspace)
    small.config.long_files = "elide"
    t = create_task(small.conn, small.log, task.contract, task.budget)
    small.backends["cloud"].queue(_read(), FINAL)
    assert run_task(small, t.id).status.startswith("done")
    assert _events(small, t, "pages.unread_held") == []


def test_a_second_task_reading_the_same_page_is_held_like_the_first(small, task, workspace):
    _long(workspace)
    holds = []
    for _ in range(2):
        t = create_task(small.conn, small.log, task.contract, task.budget.model_copy(update={"usd": 10.0}))
        small.backends["cloud"].queue(_read(), *[FINAL] * (MAX_PAGE_HOLDS + 1))
        run_task(small, t.id)
        holds.append(len(_events(small, t, "pages.unread_held")))
    assert holds == [MAX_PAGE_HOLDS, MAX_PAGE_HOLDS]
