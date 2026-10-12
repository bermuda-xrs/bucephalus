from __future__ import annotations

import json

import pytest

from agent.execute.loop import run_task
from agent.execute.say import SayError, say
from agent.kernel.tasks import create_task, load_task

FINAL = '<final>{"answer": "ok"}</final>'
LIST = '<action tool="list_dir">{"path": "work"}</action>'


def _write(content, path="work/out.txt"):
    return '<action tool="write_file">' + json.dumps({"path": path, "content": content}) + "</action>"


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


@pytest.fixture
def during(ctx, task, workspace, monkeypatch):
    ctx.config.autonomy.approve_scoped_writes = True

    def go(messages_at, *script):
        real = ctx.call_model
        n = []

        def call(t, backend, purpose, **kw):
            if purpose == "step":
                for text in messages_at.get(len(n), ()):
                    say(ctx, task.id, text)
                n.append(1)
            return real(t, backend, purpose, **kw)

        monkeypatch.setattr(ctx, "call_model", call)
        ctx.backends["cloud"].queue(*script)
        return run_task(ctx, task.id), ctx.backends["cloud"].calls

    return go


def test_a_message_sent_during_step_one_is_in_step_two_as_the_users_words(ctx, task, during):
    res, calls = during({0: ["use metric units"]}, LIST, FINAL)
    assert res.status.startswith("done")
    assert "use metric units" not in calls[0]["tail"]
    assert "use metric units" in calls[1]["tail"] and "taint=user" in calls[1]["tail"].split("use metric units")[0].rsplit("<observation", 1)[1]


def test_it_is_delivered_once_and_recorded(ctx, task, during):
    during({0: ["one", "two"]}, LIST, LIST, FINAL)
    got = _events(ctx, task, "human.message.delivered")
    assert len(got) == 1 and got[0]["count"] == 2 and got[0]["step"] == 1
    assert len(_events(ctx, task, "human.message")) == 2


def test_the_message_is_recorded_in_the_chain(ctx, task, during):
    during({0: ["remember this"]}, LIST, FINAL)
    row = ctx.conn.execute("SELECT payload_json FROM events WHERE task_id=? AND type='human.message'", (task.id,)).fetchone()
    assert json.loads(row[0])["text"] == "remember this"


def test_words_from_the_user_may_feed_a_writing_call_but_the_same_words_from_a_file_may_not(ctx, task, during, workspace):
    (workspace / "in.txt").write_text("secret-payload-9921", encoding="utf-8")
    res, calls = during({0: ["write secret-payload-9921 to work/out.txt"]}, LIST, _write("secret-payload-9921"), FINAL)
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "secret-payload-9921"


def test_the_same_string_from_a_tool_result_is_still_refused(ctx, task, workspace):
    ctx.config.autonomy.approve_scoped_writes = True
    (workspace / "in.txt").write_text("secret-payload-9921", encoding="utf-8")
    read = '<action tool="read_file">{"path": "work/in.txt"}</action>'
    ctx.backends["cloud"].queue(read, _write("secret-payload-9921"), FINAL)
    run_task(ctx, task.id)
    assert not (workspace / "out.txt").exists()


def test_a_message_sent_while_paused_arrives_on_resume_and_old_ones_do_not(ctx, task, workspace):
    from agent.kernel.tasks import set_status
    from agent.kernel.types import TaskStatus

    say(ctx, task.id, "first")
    ctx.backends["cloud"].queue(LIST, FINAL)
    run_task(ctx, task.id)
    assert "first" in ctx.backends["cloud"].calls[0]["tail"]
    t2 = create_task(ctx.conn, ctx.log, task.contract, task.budget)
    set_status(ctx.conn, ctx.log, load_task(ctx.conn, t2.id), TaskStatus.PAUSED, "test")
    say(ctx, t2.id, "while paused")
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, t2.id)
    assert "while paused" in ctx.backends["cloud"].calls[-1]["tail"] and "first" not in ctx.backends["cloud"].calls[-1]["tail"]


def test_a_finished_task_and_empty_or_huge_text_are_refused(ctx, task):
    with pytest.raises(SayError, match="empty"):
        say(ctx, task.id, "   ")
    with pytest.raises(SayError, match="too long"):
        say(ctx, task.id, "x" * 5000)
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, task.id)
    with pytest.raises(SayError, match="finished"):
        say(ctx, task.id, "late")


def test_the_console_route_takes_it(ctx, task):
    from fastapi.testclient import TestClient

    from agent.control.server import create_app

    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    assert c.post(f"/tasks/{task.id}/message", json={"text": "hi"}).status_code in (401, 403)
    c.headers["Authorization"] = "Bearer " + app.state.token
    assert c.post(f"/tasks/{task.id}/message", json={"text": "hi"}).status_code == 200
    assert c.post(f"/tasks/{task.id}/message", json={"text": " "}).status_code == 409


def test_the_console_has_a_say_command():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "src" / "agent" / "control" / "ui.js").read_text(encoding="utf-8")
    assert "/say" in js and "/message" in js
