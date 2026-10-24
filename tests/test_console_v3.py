from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app
from agent.execute.loop import run_task
from agent.kernel.types import Taint

CONTROL = Path(__file__).resolve().parents[1] / "src" / "agent" / "control"


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


LIST = '<action tool="list_dir">{"path": "work"}</action>'


def test_stop_halts_a_running_task_at_the_next_step(ctx, task, scripted, api, monkeypatch):
    scripted("cloud", LIST, LIST, LIST, '<final>{"answer": "done"}</final>')
    real = ctx.call_model
    seen = []

    def call(t, backend, purpose, **kw):
        r = real(t, backend, purpose, **kw)
        if purpose == "step":
            seen.append(1)
            if len(seen) == 1:
                assert api.post(f"/tasks/{task.id}/pause").status_code == 200
        return r

    monkeypatch.setattr(ctx, "call_model", call)
    first = run_task(ctx, task.id)
    assert first.status == "paused" and len(seen) == 1
    second = run_task(ctx, task.id)
    assert second.status.startswith("done"), second


def test_memory_lines_can_be_forgotten_over_http(ctx, api):
    ctx.memory.write("memory", "- injected instruction from a test", Taint.USER)
    ctx.memory.write("memory", "- a fact worth keeping", Taint.USER)
    r = api.post("/memory/forget", json={"text": "injected instruction"})
    assert r.status_code == 200 and len(r.json()["removed"]) == 1
    texts = [e.text for e in ctx.memory.entries("memory")]
    assert not any("injected" in t for t in texts) and any("worth keeping" in t for t in texts)
    assert api.get("/memory").json()["blessing"] == "intact"
    assert api.post("/memory/forget", json={"text": "  "}).status_code == 400


def test_schedules_can_be_added_and_removed_over_http(ctx, api):
    r = api.post("/schedules", json={"spec": "0 9 * * 1", "goal": "summarise the week", "scope": "work"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    listed = api.get("/panels/schedules").json()["schedules"]
    assert [s["id"] for s in listed] == [sid] and listed[0]["goal"] == "summarise the week"
    assert api.post("/schedules", json={"spec": "every day", "goal": "x"}).status_code == 400
    assert api.delete(f"/schedules/{sid}").status_code == 200
    assert api.get("/panels/schedules").json()["schedules"] == []
    assert api.delete(f"/schedules/{sid}").status_code == 404


def test_a_skill_body_can_be_read(ctx, api):
    sid = api.post("/skills", json={"name": "verbatim", "body": "# verbatim\n\nQuote it.",
                                    "applies_when": "asked what a file says"}).json()["id"]
    d = api.get(f"/skills/{sid}").json()
    assert d["body"].startswith("# verbatim") and d["status"] == "candidate"
    assert api.get("/skills/nope").status_code == 404


def _conversation_with_an_answer(ctx, api, scripted):
    from agent.kernel.tasks import load_task
    from agent.plan.planner import approve_plan, store_plan
    from agent.plan.types import Plan, PlanStep

    cid = api.post("/conversations", json={}).json()["id"]
    tid = api.post(f"/conversations/{cid}/messages", json={"text": "list the files"}).json()["task_id"]
    t = load_task(ctx.conn, tid)
    approve_plan(ctx, t, store_plan(ctx, t, Plan(objective="read the folder",
                                                  steps=[PlanStep(id="1", intent="list it")]).seal()))
    scripted("cloud", '<final>{"answer": "alpha.txt and **beta.txt**"}</final>')
    run_task(ctx, tid)
    return cid, tid


def test_a_conversation_exports_as_markdown_and_json(ctx, api, scripted):
    cid, tid = _conversation_with_an_answer(ctx, api, scripted)
    md = api.get(f"/conversations/{cid}/export?format=md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"]
    body = md.text
    assert "list the files" in body and "read the folder" in body and "beta.txt" in body
    js = api.get(f"/conversations/{cid}/export?format=json")
    assert js.json()["items"][0]["task_id"] == tid
    assert api.get(f"/conversations/{cid}/export?format=pdf").status_code == 400


def test_pinned_conversations_list_first(ctx, api):
    a = api.post("/conversations", json={"title": "older"}).json()["id"]
    b = api.post("/conversations", json={"title": "newer"}).json()["id"]
    ctx.conn.execute("UPDATE conversations SET updated_at = updated_at + 60 WHERE id=?", (b,))
    assert [c["id"] for c in api.get("/conversations").json()["conversations"]][0] == b
    assert api.patch(f"/conversations/{a}", json={"pinned": True}).json()["pinned"] is True
    listed = api.get("/conversations").json()["conversations"]
    assert [c["id"] for c in listed] == [a, b] and listed[0]["pinned"] is True


def test_a_thread_reports_what_the_conversation_spent(ctx, api, scripted):
    cid, _ = _conversation_with_an_answer(ctx, api, scripted)
    th = api.get(f"/conversations/{cid}").json()
    assert th["spent"]["usd"] > 0 and th["spent"]["seconds"] >= 0


NODE = shutil.which("node")


def _md(text: str) -> str:
    script = (f"const m = require({json.dumps(str(CONTROL / 'md.js'))});"
              f"process.stdout.write(m.renderMarkdown({json.dumps(text)}));")
    return subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_markdown_renders_the_common_forms():
    out = _md("# Title\n\nSome **bold** and `code`.\n\n- one\n- two\n\n```py\nx = 1 < 2\n```")
    assert "<h4>Title</h4>" in out and "<strong>bold</strong>" in out and "<code>code</code>" in out
    assert "<li>one</li>" in out and '<pre class="code"><code>x = 1 &lt; 2\n</code></pre>' in out


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_markdown_never_emits_markup_from_the_answer():
    out = _md('<script>alert(1)</script> <img src=x onerror=alert(1)> [x](javascript:alert(1)) '
              '[ok](https://example.com) **<b>x</b>**')
    assert "<script" not in out and "<img" not in out and 'href="javascript' not in out
    assert '<a href="https://example.com" rel="noopener noreferrer" target="_blank">ok</a>' in out
    assert "<b>" not in out


def test_the_console_loads_the_renderer_before_the_app():
    html = (CONTROL / "ui.html").read_text(encoding="utf-8")
    assert html.index('src="/md.js"') < html.index('src="/ui.js"')


UI = (CONTROL / "ui.js").read_text(encoding="utf-8")
HTML = (CONTROL / "ui.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("route", ["/pause", "/memory/forget", "/schedules", "/skills/", "/export?format="])
def test_the_console_uses_each_new_route(route):
    assert route in UI


def test_answers_render_through_the_safe_renderer():
    assert "renderMarkdown(" in UI
    assert "<pre>${esc(it.answer)}</pre>" not in UI


def test_every_action_the_page_names_has_a_handler():
    import re

    named = set(re.findall(r'data-action="([A-Za-z]+)"', UI + HTML))
    block = UI[UI.index("const ACTIONS = {"):]
    handlers = set(re.findall(r"\b([A-Za-z]+)\b", block[:block.index("};")]))
    assert named and not (named - handlers), named - handlers


def test_the_thread_shows_the_answer_not_its_envelope(ctx, api, scripted):
    cid, _ = _conversation_with_an_answer(ctx, api, scripted)
    assert api.get(f"/conversations/{cid}").json()["items"][0]["answer"] == "alpha.txt and **beta.txt**"


def test_a_structured_answer_is_shown_as_json():
    from agent.control.conversations import shown_answer

    assert shown_answer('{"answer": {"files": 2}}').startswith("```json")
    assert shown_answer("plain text") == "plain text"


def test_a_task_alone_also_shows_the_answer_not_its_envelope(ctx, api, scripted):
    _, tid = _conversation_with_an_answer(ctx, api, scripted)
    t = api.get(f"/tasks/{tid}").json()
    assert t["answer_shown"] == "alpha.txt and **beta.txt**" and "answer" in json.loads(t["answer"])
    assert "answer: t.answer_shown" in UI


def test_latest_means_last_written_even_within_one_clock_tick(ctx, api, monkeypatch):
    import time as _t

    from agent.kernel.tasks import load_task
    from agent.ledger.artifacts import write_artifact
    from agent.plan.planner import load_plan, store_plan
    from agent.plan.types import Plan, PlanStep

    cid = api.post("/conversations", json={}).json()["id"]
    tid = api.post(f"/conversations/{cid}/messages", json={"text": "x"}).json()["task_id"]
    t = load_task(ctx.conn, tid)
    monkeypatch.setattr(_t, "time", lambda: 1_790_000_000.0)
    store_plan(ctx, t, Plan(objective="first", steps=[PlanStep(id="1", intent="a")]).seal())
    second = store_plan(ctx, t, Plan(objective="second", steps=[PlanStep(id="1", intent="b")]).seal())
    write_artifact(ctx.conn, tid, "answer", "older", Taint.TRUSTED, {})
    write_artifact(ctx.conn, tid, "answer", "newer", Taint.TRUSTED, {})
    assert load_plan(ctx, tid).id == second.id
    item = api.get(f"/conversations/{cid}").json()["items"][0]
    assert item["plan"]["body"]["objective"] == "second" and item["answer"] == "newer"
    assert api.get(f"/tasks/{tid}").json()["answer"] == "newer"


def test_markdown_files_preview_rendered_and_others_as_text():
    assert "isMarkdownFile(" in UI and 'id="filePreview"' in HTML
    assert "(md|markdown)$" in UI and "renderMarkdown(d.text)" in UI


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_emphasis_markers_inside_a_link_url_leave_the_url_alone():
    out = _md("see [the doc](https://example.com/a_b_/**c**) and _this_")
    assert '<a href="https://example.com/a_b_/**c**" rel="noopener noreferrer" target="_blank">the doc</a>' in out
    assert "<em>this</em>" in out


def test_the_suite_table_cuts_a_goal_before_escaping_it():
    assert "esc(t.goal).slice(" not in UI
