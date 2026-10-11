from __future__ import annotations

import json

import pytest

from agent.execute import shadow
from agent.execute.loop import run_task
from agent.execute.rewind import RewindError, list_checkpoints, rewind
from agent.kernel.tasks import create_task, load_task

pytestmark = pytest.mark.skipif(not shadow.git_available(), reason="git is not installed")

FINAL = '<final>{"answer": "ok"}</final>'


def _write(content, path="work/a.txt"):
    return '<action tool="write_file">' + json.dumps({"path": path, "content": content}) + "</action>"


@pytest.fixture
def run(ctx, task, workspace):
    ctx.config.mechanisms.shadow_checkpoints = True
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.backends["cloud"].queue(_write("one"), _write("twotwo"), FINAL)
    run_task(ctx, task.id)
    return ctx, task, workspace


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


def test_each_step_is_checkpointed_before_it_runs(run):
    ctx, task, _ = run
    cps = list_checkpoints(ctx, task.id)
    assert [c["step"] for c in cps] == [0, 1, 2]
    assert all(len(c["sha"]) == 40 for c in cps) and len({c["sha"] for c in cps}) == 3


def test_rewinding_code_restores_the_files_and_leaves_the_conversation(run):
    ctx, task, ws = run
    before = len(_events(ctx, task, "task.park_seed"))
    rewind(ctx, task.id, step=1, what="code")
    assert (ws / "a.txt").read_text(encoding="utf-8") == "one"
    assert len(_events(ctx, task, "task.park_seed")) == before
    assert _events(ctx, task, "task.rewound")[-1]["what"] == "code"


def test_a_rewind_can_itself_be_undone(run):
    ctx, task, ws = run
    out = rewind(ctx, task.id, step=0, what="code")
    assert not (ws / "a.txt").exists()
    shadow.ShadowRepo.for_task(ctx, load_task(ctx.conn, task.id)).restore(out["undo"])
    assert (ws / "a.txt").read_text(encoding="utf-8") == "twotwo"


def test_rewinding_the_conversation_leaves_the_files(run):
    ctx, task, ws = run
    rewind(ctx, task.id, step=1, what="conversation")
    assert (ws / "a.txt").read_text(encoding="utf-8") == "twotwo"
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, task.id)
    tail = ctx.backends["cloud"].calls[-1]["tail"]
    assert '"bytes": 3' in tail and '"bytes": 6' not in tail


def test_rewinding_both_does_both_and_a_repeated_call_really_runs(run):
    ctx, task, ws = run
    rewind(ctx, task.id, step=0, what="both")
    assert not (ws / "a.txt").exists()
    assert load_task(ctx.conn, task.id).status.value == "paused"
    ctx.backends["cloud"].queue(_write("one"), FINAL)
    run_task(ctx, task.id)
    assert (ws / "a.txt").read_text(encoding="utf-8") == "one"


def test_the_default_is_the_latest_checkpoint_and_both(run):
    ctx, task, ws = run
    out = rewind(ctx, task.id)
    assert out["step"] == 2 and out["what"] == "both"
    assert (ws / "a.txt").read_text(encoding="utf-8") == "twotwo"


def test_an_unknown_step_or_mode_is_refused(run):
    ctx, task, _ = run
    with pytest.raises(RewindError, match="no checkpoint at step 9"):
        rewind(ctx, task.id, step=9)
    with pytest.raises(RewindError, match="what must be"):
        rewind(ctx, task.id, step=1, what="everything")


def test_a_running_task_is_not_rewound(run):
    from agent.kernel.tasks import set_status
    from agent.kernel.types import TaskStatus

    ctx, task, _ = run
    set_status(ctx.conn, ctx.log, load_task(ctx.conn, task.id), TaskStatus.RUNNING)
    with pytest.raises(RewindError, match="running"):
        rewind(ctx, task.id, step=1)


def test_without_git_the_task_still_runs_and_says_so(ctx, task, workspace, monkeypatch):
    ctx.config.mechanisms.shadow_checkpoints = True
    ctx.config.autonomy.approve_scoped_writes = True
    monkeypatch.setattr(shadow, "git_available", lambda: False)
    ctx.backends["cloud"].queue(_write("one"), FINAL)
    assert run_task(ctx, task.id).status.startswith("done")
    assert _events(ctx, task, "checkpoint.skipped") and not _events(ctx, task, "checkpoint.shadow")


def test_the_mechanism_can_be_ablated(ctx, task, workspace):
    ctx.config.mechanisms.shadow_checkpoints = False
    ctx.backends["cloud"].queue(_write("one"), FINAL)
    run_task(ctx, task.id)
    assert not _events(ctx, task, "checkpoint.shadow")
    assert "shadow_checkpoints" in ctx.config.mechanisms.ablated()


def test_the_shadow_directory_is_protected_state(ctx, task):
    from agent.security.capabilities import is_protected, task_state_paths

    root = str(ctx.config.root)
    assert is_protected("shadow/t1/HEAD", root, task_state_paths(ctx, task))


def test_the_model_cannot_read_its_own_checkpoints(run):
    ctx, task, _ = run
    t = create_task(ctx.conn, ctx.log, task.contract, task.budget)
    ctx.backends["cloud"].queue(
        '<action tool="read_file">' + json.dumps({"path": f"shadow/{task.id}/HEAD"}) + "</action>", FINAL)
    run_task(ctx, t.id)
    assert "protected path" in ctx.backends["cloud"].calls[-1]["tail"]


def test_the_last_rewind_can_be_undone(run):
    from agent.execute.rewind import undo

    ctx, task, ws = run
    rewind(ctx, task.id, step=0, what="code")
    assert not (ws / "a.txt").exists()
    undo(ctx, task.id)
    assert (ws / "a.txt").read_text(encoding="utf-8") == "twotwo"
    with pytest.raises(RewindError, match="nothing to undo"):
        undo(ctx, task.id)


@pytest.fixture
def api(run):
    from fastapi.testclient import TestClient

    from agent.control.server import create_app

    app = create_app(run[0])
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_the_console_lists_and_rewinds(api, run):
    ctx, task, ws = run
    got = api.get(f"/tasks/{task.id}/checkpoints").json()
    assert [c["step"] for c in got] == [0, 1, 2] and "sha" in got[0]
    r = api.post(f"/tasks/{task.id}/rewind", json={"step": 1, "what": "code"})
    assert r.status_code == 200 and r.json()["step"] == 1
    assert (ws / "a.txt").read_text(encoding="utf-8") == "one"
    assert api.post(f"/tasks/{task.id}/rewind", json={"step": 9, "what": "code"}).status_code == 409
    assert api.post(f"/tasks/{task.id}/rewind", json={"what": "undo"}).status_code == 200
    assert (ws / "a.txt").read_text(encoding="utf-8") == "twotwo"


def test_the_console_has_a_rewind_control():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "src" / "agent" / "control" / "ui.js").read_text(encoding="utf-8")
    assert "showRewind" in js and "doRewind" in js and "/rewind" in js
