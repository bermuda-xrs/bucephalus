"""The console (control/ui.html) and the routes it added for plan review.

Two rules are held structurally, because a stylesheet has no runtime to test:

  * THE INTEGRITY COLOUR IS RESERVED. `--danger` may style `.integrity` and
    nothing else - a broken hash chain must never look like a permission
    request, or it will one day be approved by muscle memory (theme file).
  * THE BANNER IS DISPLAY ONLY. It reaches the page through /theme and no
    prompt ever carries it (tests/test_tui.py greps every prompt).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agent import desktop

CONTROL = Path(__file__).resolve().parents[1] / "src" / "agent" / "control"
UI = CONTROL / "ui.html"
# The console's styles and script are files since the CSP (iteration 3, 1.5).
CSS, JS = CONTROL / "ui.css", CONTROL / "ui.js"


def _all_ui() -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(CONTROL.iterdir())
                   if p.suffix in (".html", ".css", ".js"))


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def test_the_integrity_colour_styles_integrity_and_nothing_else():
    css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)
    offenders = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "var(--danger)" in body and not all(
                s.strip().startswith(".integrity") for s in selector.split(",")):
            offenders.append(selector.strip())
    assert not offenders, f"--danger used outside .integrity: {offenders}"
    literal = re.findall(r"#ff4b4b", _all_ui(), re.I)
    assert len(literal) == 1, "the danger colour is spelled out somewhere besides :root"


def test_the_console_makes_no_stale_claim_about_verifiers():
    """checker_program was removed and execution refuses without a script
    (DR7); a page that still counted them as checking correctness would tell
    the user a shape-only suite was sound."""
    text = _all_ui()
    assert "checker_program" not in text
    counted = re.search(r"function checksCorrectness.*?\n}", text, re.S).group(0)
    assert '"execution"' not in counted


@pytest.fixture
def plane(ctx, workspace):
    server, thread, port = desktop.start_server(ctx, "127.0.0.1")
    token = server.config.app.state.token

    def call(method, path, body=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    yield call
    desktop.shutdown(server, thread)


def _draft(ctx, task_id):
    from agent.plan.types import Plan, PlanStep

    plan = Plan(objective="o", steps=[PlanStep(id="1", intent="read it")]).seal()
    ctx.conn.execute("INSERT INTO plans(id, task_id, body_json, status, approved_hash, "
                     "created_at) VALUES (?,?,?,?,?,?)",
                     (plan.id, task_id, json.dumps(plan.body()), "draft", None, time.time()))
    ctx.conn.commit()
    return plan


def test_the_queue_lists_tasks_with_their_plan_state(ctx, plane):
    t = plane("POST", "/tasks", {"goal": "list the files", "artifact_scope": ["work"]})
    row = next(x for x in plane("GET", "/tasks")["tasks"] if x["task_id"] == t["task_id"])
    assert row["plan_status"] is None and row["goal"] == "list the files"
    _draft(ctx, t["task_id"])
    row = next(x for x in plane("GET", "/tasks")["tasks"] if x["task_id"] == t["task_id"])
    assert row["plan_status"] == "draft"


def test_a_plan_can_be_read_and_sent_back(ctx, plane):
    t = plane("POST", "/tasks", {"goal": "g", "artifact_scope": ["work"]})
    with pytest.raises(urllib.error.HTTPError) as e:
        plane("GET", f"/tasks/{t['task_id']}/plan")
    assert e.value.code == 404
    plan = _draft(ctx, t["task_id"])
    got = plane("GET", f"/tasks/{t['task_id']}/plan")
    assert got["plan_id"] == plan.id and got["status"] == "draft"
    plane("POST", f"/tasks/{t['task_id']}/plan/reject",
          {"plan_id": plan.id, "comment": "count reopened tickets as open"})
    assert plane("GET", f"/tasks/{t['task_id']}/plan")["status"] == "rejected"
    comments = [json.loads(p)["comment"] for (p,) in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='plan.rejected'",
        (t["task_id"],))]
    assert comments == ["count reopened tickets as open"], "the reason must be on the record"


def test_the_theme_route_serves_the_theme_files_colours_and_banner(plane):
    from agent.tui.theme import load

    pkg = Path(desktop.__file__).resolve().parent
    theme = plane("GET", "/theme")
    assert theme["colors"]["danger"] == load(pkg / "themes" / "default.toml", pkg)["colors"]["danger"]
    art = (pkg / "assets" / "banner.ans").read_text(encoding="utf-8").rstrip("\n")
    assert theme["banner"] == art, "the banner is not the theme's banner"
