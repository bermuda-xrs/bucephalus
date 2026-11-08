from __future__ import annotations

import base64
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import schedule as sched
from agent.control.server import create_app
from agent.kernel.integrity import critical, verify_all
from agent.kernel.tasks import create_task, load_task, set_status
from agent.kernel.types import Budget, TaskContract, TaskStatus


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def _base() -> float:
    d = datetime.now().replace(minute=0, second=0, microsecond=0)
    return (d - timedelta(hours=3)).timestamp()


def _sched(ctx, spec="* * * * *", created=None, sealed=True, goal="summarise the work folder"):
    return sched.add(ctx.conn, spec, TaskContract(goal=goal, artifact_scope=["work"]),
                     Budget(usd=1.0, seconds=600), now=created, anchor=ctx.anchor if sealed else None)


def _actions(out):
    return [r["action"] for r in out]


def _scheduled(ctx) -> int:
    return ctx.conn.execute("SELECT COUNT(*) FROM tasks WHERE origin='schedule'").fetchone()[0]


# ---------------------------------------------------------------- schedules

def test_a_schedule_is_sealed_in_the_anchor_when_added(ctx):
    sid = _sched(ctx)
    assert ctx.anchor.latest(f"schedule:{sid}") == sched.seal(sched.get(ctx.conn, sid))


def test_a_changed_schedule_never_fires_pauses_itself_and_verify_flags_it(ctx):
    base = _base()
    sid = _sched(ctx, created=base)
    ctx.conn.execute("UPDATE schedules SET contract_json=replace(contract_json, 'summarise', 'delete') WHERE id=?",
                     (sid,))
    ctx.conn.commit()
    assert _actions(sched.tick(ctx, now=base + 65)) == ["tampered"]
    assert _scheduled(ctx) == 0
    assert sched.get(ctx.conn, sid)["paused_at"], "a changed schedule stops itself"
    found = [f for f in critical(verify_all(ctx.conn, ctx.log, ctx.anchor)) if sid in f.get("detail", "")]
    assert found and found[0]["task_id"] == f"schedule:{sid}"
    assert sched.run_now(ctx, sid, now=base + 90)["action"] == "tampered" and _scheduled(ctx) == 0


def test_the_anchor_entries_of_schedules_are_not_deleted_tasks(ctx):
    _sched(ctx)
    assert not critical(verify_all(ctx.conn, ctx.log, ctx.anchor))


def test_a_schedule_from_before_the_seal_is_sealed_on_its_first_tick(ctx):
    base = _base()
    sid = _sched(ctx, created=base, sealed=False)
    assert ctx.anchor.latest(f"schedule:{sid}") is None
    assert _actions(sched.tick(ctx, now=base + 65)) == ["fired"]
    assert ctx.anchor.latest(f"schedule:{sid}") == sched.seal(sched.get(ctx.conn, sid))


def test_a_paused_schedule_records_its_occurrences_and_never_runs_them_late(ctx):
    base = _base()
    sid = _sched(ctx, created=base)
    assert sched.pause(ctx.conn, sid) and not sched.pause(ctx.conn, sid)
    out = sched.tick(ctx, now=base + 185)
    assert _actions(out) == ["missed"] and out[0]["reason"] == "paused" and _scheduled(ctx) == 0
    assert sched.resume(ctx.conn, sid) and not sched.resume(ctx.conn, sid)
    out = sched.tick(ctx, now=base + 245)
    assert _actions(out) == ["fired"] and out[0]["occurrence"] == base + 240


def test_run_now_fires_once_through_the_same_path(ctx):
    base = _base()
    sid = _sched(ctx, spec="0 3 * * *", created=base)
    out = sched.run_now(ctx, sid, now=base + 30)
    assert out["action"] == "fired"
    t = load_task(ctx.conn, out["task_id"])
    assert t.origin == "schedule" and t.contract.goal == "summarise the work folder"
    assert any(e["type"] == "schedule.fired" for e in ctx.log.events(t.id))
    with pytest.raises(sched.Busy):
        sched.run_now(ctx, sid, now=base + 40)
    with pytest.raises(LookupError):
        sched.run_now(ctx, "c_nosuch", now=base + 40)


def test_run_now_records_the_occurrences_it_stands_in_for(ctx):
    base = _base()
    sid = _sched(ctx, created=base)
    out = sched.run_now(ctx, sid, now=base + 600 + 30)
    assert out["action"] == "fired" and out["occurrence"] == int(base + 630)
    missed = ctx.conn.execute("SELECT COUNT(*), MAX(occurrence) FROM schedule_firings WHERE schedule_id=? "
                              "AND outcome='missed'", (sid,)).fetchone()
    assert missed == (1, int(base + 600))
    assert sched.next_fire(ctx.conn, sched.get(ctx.conn, sid)) == base + 660


def test_an_edit_is_a_new_sealed_schedule_and_the_old_one_is_removed(ctx):
    sid = _sched(ctx)
    sched.pause(ctx.conn, sid)
    new = sched.replace(ctx.conn, sid, "0 9 * * 1", TaskContract(goal="weekly", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=600), anchor=ctx.anchor)
    ids = [s["id"] for s in sched.active(ctx.conn)]
    assert new != sid and ids == [new]
    row = sched.get(ctx.conn, new)
    assert row["paused_at"], "an edit keeps a paused schedule paused"
    assert ctx.anchor.latest(f"schedule:{new}") == sched.seal(row)
    assert sched.replace(ctx.conn, sid, "0 9 * * 1", TaskContract(goal="x"), Budget(usd=1.0, seconds=60),
                         anchor=ctx.anchor) is None, "a removed schedule cannot be edited"
    with pytest.raises(sched.CronError):
        sched.replace(ctx.conn, new, "bad", TaskContract(goal="x"), Budget(usd=1.0, seconds=60), anchor=ctx.anchor)
    assert [s["id"] for s in sched.active(ctx.conn)] == [new]


def test_the_schedule_routes(ctx, api):
    sid = api.post("/schedules", json={"spec": "0 3 * * *", "goal": "nightly", "scope": "work"}).json()["id"]
    assert ctx.anchor.latest(f"schedule:{sid}")
    assert api.post(f"/schedules/{sid}/pause").json() == {"id": sid, "paused": True}
    assert api.post(f"/schedules/{sid}/pause").status_code == 409
    assert api.post(f"/schedules/{sid}/resume").json() == {"id": sid, "paused": False}
    tid = api.post(f"/schedules/{sid}/run").json()["task_id"]
    assert load_task(ctx.conn, tid).origin == "schedule"
    assert api.post(f"/schedules/{sid}/run").status_code == 409
    new = api.put(f"/schedules/{sid}", json={"spec": "0 4 * * *", "goal": "nightly, later", "scope": "work"})
    assert new.status_code == 200 and new.json()["id"] != sid
    assert api.put(f"/schedules/{sid}", json={"spec": "0 4 * * *", "goal": "x"}).status_code == 404
    for verb in ("pause", "resume", "run"):
        assert api.post(f"/schedules/c_nosuch/{verb}").status_code == 404
    rows = api.get("/panels/schedules").json()["schedules"]
    assert [r["id"] for r in rows] == [new.json()["id"]] and rows[0]["paused"] is False


# ---------------------------------------------------------------- sessions

def _conv(ctx, title, days_ago, pinned=False, task_status=None):
    from agent.control import conversations as conv

    cid = conv.create(ctx.conn, title)["id"]
    if task_status:
        t = create_task(ctx.conn, ctx.log, TaskContract(goal=title), Budget(usd=1.0, seconds=60))
        ctx.conn.execute("INSERT INTO conversation_tasks(task_id, conversation_id, position) VALUES (?,?,0)",
                         (t.id, cid))
        if task_status != "pending":
            set_status(ctx.conn, ctx.log, t, TaskStatus(task_status))
    ctx.conn.execute("UPDATE conversations SET updated_at=?, pinned=? WHERE id=?",
                     (time.time() - days_ago * 86400, int(pinned), cid))
    ctx.conn.commit()
    return cid


def test_archive_older_counts_first_and_spares_pinned_and_unfinished(ctx, api):
    old = _conv(ctx, "old", 40, task_status="done")
    _conv(ctx, "old pinned", 40, pinned=True)
    _conv(ctx, "old and waiting", 40, task_status="pending")
    _conv(ctx, "recent", 2)
    assert api.post("/conversations/archive_older", json={"days": 30, "dry_run": True}).json() == {"count": 1}
    assert all(not c["archived"] for c in api.get("/conversations").json()["conversations"])
    assert api.post("/conversations/archive_older", json={"days": 30}).json() == {"count": 1}
    assert [c["id"] for c in api.get("/conversations?archived=true").json()["conversations"]] == [old]
    assert api.post("/conversations/archive_older", json={"days": 0}).status_code == 422


# ---------------------------------------------------------------- files

def _put(api, tid, path, data=b"dropped in\n"):
    return api.post(f"/tasks/{tid}/files", json={"path": path, "content_b64": base64.b64encode(data).decode()})


@pytest.fixture
def tid(ctx, workspace):
    (workspace / "a.txt").write_bytes(b"alpha\n")
    return create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=60)).id


def test_a_folder_is_made_inside_the_workspace_only(ctx, api, tid):
    root = Path(ctx.config.root)
    assert api.post(f"/tasks/{tid}/files/mkdir", json={"path": "work/new/deeper"}).status_code == 200
    assert (root / "work" / "new" / "deeper").is_dir()
    assert any(e["type"] == "human.made_folder" for e in ctx.log.events(tid))
    assert api.post(f"/tasks/{tid}/files/mkdir", json={"path": "work/new/deeper"}).status_code == 409
    for bad in ("outside/x", "work/../outside", "grading/x", "config.yaml"):
        assert api.post(f"/tasks/{tid}/files/mkdir", json={"path": bad}).status_code == 403, bad
        assert not (root / bad).exists()


def test_only_a_file_you_added_and_nobody_changed_can_be_removed(ctx, api, tid):
    root = Path(ctx.config.root)
    assert _put(api, tid, "work/dropped.txt").status_code == 200
    assert _put(api, tid, "work/changed.txt").status_code == 200
    (root / "work" / "changed.txt").write_bytes(b"the agent rewrote this\n")
    assert api.delete(f"/tasks/{tid}/files?path=work/a.txt").status_code == 403
    assert api.delete(f"/tasks/{tid}/files?path=work/changed.txt").status_code == 403
    assert (root / "work" / "changed.txt").exists()
    assert api.delete(f"/tasks/{tid}/files?path=work/nothing.txt").status_code == 404
    r = api.delete(f"/tasks/{tid}/files?path=work/dropped.txt")
    assert r.status_code == 200 and not (root / "work" / "dropped.txt").exists()
    ev = [e for e in ctx.log.events(tid) if e["type"] == "human.removed"]
    assert ev and ev[-1]["payload"]["path"] == "work/dropped.txt"
    assert ctx.log.verify(tid)[0]


def test_nothing_is_removed_while_the_task_runs(ctx, api, tid):
    assert _put(api, tid, "work/dropped.txt").status_code == 200
    set_status(ctx.conn, ctx.log, load_task(ctx.conn, tid), TaskStatus.RUNNING)
    assert api.delete(f"/tasks/{tid}/files?path=work/dropped.txt").status_code == 409
    assert (Path(ctx.config.root) / "work" / "dropped.txt").exists()


NEW_CALLS = [("POST", "/conversations/archive_older"), ("POST", "/tasks/{}/files/mkdir"), ("DELETE", "/tasks/{}/files"),
             ("PUT", "/schedules/{}"), ("POST", "/schedules/{}/pause"), ("POST", "/schedules/{}/resume"),
             ("POST", "/schedules/{}/run")]


def test_the_pages_offer_every_new_control():
    from test_console_reskin_guards import HTML, UI, _calls

    used = _calls(UI)
    assert not [c for c in NEW_CALLS if c not in used]
    for act in ("archiveOlder", "newFolder", "removeFile", "pauseSchedule", "resumeSchedule", "runSchedule",
                "editSchedule"):
        assert f'data-action="{act}"' in UI + HTML or f'"{act}"' in UI, act
