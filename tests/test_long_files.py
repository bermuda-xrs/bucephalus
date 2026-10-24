from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.config import Config
from agent.execute.loop import PAGE_MAX_TOKENS, _ceiling_half, _inline_cap, _render_result, page_tokens, run_task
from agent.execute.tools import tool_read_file
from agent.tokens import count_tokens

CONTROL = Path(__file__).resolve().parents[1] / "src" / "agent" / "control"
FINAL = '<final>{"answer": "ok"}</final>'


def _long(workspace, lines=2000):
    text = "".join(f"line {i}: the handbook says nothing in particular here\n" for i in range(lines))
    (workspace / "long.md").write_text(text, encoding="utf-8")
    return text


def test_pages_is_the_default():
    assert Config.model_fields["long_files"].default == "pages"


def test_a_long_file_comes_in_pages_that_fit_the_inline_cap_and_join_to_the_file(ctx, task, workspace):
    text = _long(workspace)
    first = tool_read_file(ctx, task, "work/long.md")
    assert first["page"] == 1 and first["pages"] > 1
    assert count_tokens(_render_result(first)) <= _ceiling_half(ctx)
    assert first["content"].rstrip().endswith("read_file(path='work/long.md', page=2)]")
    parts = [tool_read_file(ctx, task, "work/long.md", page=n) for n in range(1, first["pages"] + 1)]
    assert "".join(p["content"].rsplit("\n[", 1)[0] if p["page"] < p["pages"] else p["content"] for p in parts) == text
    assert "next" not in parts[-1]["content"].rsplit("\n", 2)[-1]


def test_a_page_fits_the_context_whatever_the_inline_cap(ctx):
    assert page_tokens(ctx) == max(200, min(PAGE_MAX_TOKENS, int(_ceiling_half(ctx) * 0.8)))
    assert page_tokens(ctx) > _inline_cap(ctx) or _inline_cap(ctx) >= _ceiling_half(ctx)


def test_a_paged_read_is_neither_cut_nor_summarised(ctx, task, workspace):
    _long(workspace)
    ctx.config.compress_observations_over_tokens = 50
    ctx.backends["cloud"].queue('<action tool="read_file">{"path": "work/long.md"}</action>', FINAL)
    run_task(ctx, task.id)
    first = tool_read_file(ctx, task, "work/long.md")["content"]
    assert first.splitlines()[-2] in ctx.backends["cloud"].calls[1]["tail"]


def test_a_page_that_does_not_exist_is_refused(ctx, task, workspace):
    _long(workspace)
    n = tool_read_file(ctx, task, "work/long.md")["pages"]
    for bad in (0, n + 1):
        r = tool_read_file(ctx, task, "work/long.md", page=bad)
        assert r["ok"] is False and f"has {n} pages" in r["error"]


def test_a_short_file_is_one_page_with_no_pointer(ctx, task, workspace):
    (workspace / "s.txt").write_text("hello\n", encoding="utf-8")
    r = tool_read_file(ctx, task, "work/s.txt")
    assert (r["content"], r["page"], r["pages"]) == ("hello\n", 1, 1)


def test_elide_mode_reads_the_whole_file_as_before(ctx, task, workspace):
    ctx.config.long_files = "elide"
    text = _long(workspace)
    r = tool_read_file(ctx, task, "work/long.md")
    assert r["content"] == text and "page" not in r


def test_whole_mode_raises_the_fill_ratio_and_so_the_cap(ctx):
    from agent.config import fill_ratio

    base = _inline_cap(ctx)
    ctx.config.long_files = "whole"
    assert fill_ratio(ctx.config) == max(ctx.config.limits.context_fill_ratio, 0.75)
    assert _inline_cap(ctx) >= base


def test_in_a_run_the_first_page_is_shown_without_elision(ctx, task, workspace):
    _long(workspace)
    ctx.backends["cloud"].queue('<action tool="read_file">{"path": "work/long.md"}</action>', FINAL)
    run_task(ctx, task.id)
    tail = ctx.backends["cloud"].calls[1]["tail"]
    assert "elided" not in tail and "page=2)]" in tail


def test_the_mode_is_recorded_with_the_run(ctx, task):
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, task.id)
    row = ctx.conn.execute("SELECT payload_json FROM events WHERE task_id=? AND type='session.opened'", (task.id,)).fetchone()
    assert json.loads(row[0])["long_files"] == "pages"


@pytest.fixture
def api(ctx):
    from fastapi.testclient import TestClient

    from agent.control.server import create_app

    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_the_console_shows_and_sets_the_mode_for_new_tasks(ctx, api):
    assert api.get("/panels/settings").json()["long_files"] == {"mode": "pages", "choices": ["pages", "elide", "whole"]}
    assert api.post("/settings/long_files", json={"mode": "whole"}).json() == {"mode": "whole"}
    assert ctx.config.long_files == "whole"
    assert api.post("/settings/long_files", json={"mode": "bogus"}).status_code == 422


def test_the_console_will_not_change_it_under_a_running_task(ctx, api, monkeypatch):
    from agent.control import server

    class Alive:
        def is_alive(self):
            return True

    monkeypatch.setitem(server._runs, "t_x", Alive())
    assert api.post("/settings/long_files", json={"mode": "elide"}).status_code == 409
    assert ctx.config.long_files == "pages"


def test_the_settings_pane_has_the_selector():
    js = (CONTROL / "ui.js").read_text(encoding="utf-8")
    assert 'id="longFiles"' in js and "/settings/long_files" in js


def test_read_file_on_a_directory_says_so(ctx, task, workspace):
    from agent.execute.tools import tool_read_file

    (workspace / "sub").mkdir()
    out = tool_read_file(ctx, task, "work/sub")
    assert out["ok"] is False and "not a file" in out["error"]


def test_read_file_reads_no_more_than_its_cap(ctx, task, workspace, monkeypatch):
    from pathlib import Path

    from agent.execute import tools

    (workspace / "big.txt").write_text("x" * 50, encoding="utf-8")
    monkeypatch.setattr(tools, "MAX_READ_CHARS", 10)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read the whole file")))
    assert tools.tool_read_file(ctx, task, "work/big.txt")["content"].startswith("x" * 10)
