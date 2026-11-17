from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.control import conversations
from agent.control.server import create_app
from agent.kernel.integrity import critical, verify_all
from agent.kernel.types import Taint
from agent.ledger.artifacts import write_artifact
from test_console_reskin_guards import UI, _calls


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def _say(ctx, cid, text, answer=True):
    tid = conversations.add_message(ctx, cid, text, ["work"], None, None)["task_id"]
    if answer:
        write_artifact(ctx.conn, tid, "answer", f"answer to {text}", Taint.TRUSTED, {"by": "test"})
    return tid


@pytest.fixture
def conv(ctx):
    cid = conversations.create(ctx.conn, "Trip plan")["id"]
    return cid, [_say(ctx, cid, f"q{i}") for i in range(4)]


def _ids(ctx, cid):
    return [i["task_id"] for i in conversations.thread(ctx, cid)["items"]]


def branch(api, cid, after):
    return api.post(f"/conversations/{cid}/branch", json={"after_task": after})


def merge(api, cid, other):
    return api.post(f"/conversations/{cid}/merge", json={"other": other})


def test_cutting_moves_the_later_exchanges_to_a_new_conversation(ctx, api, conv):
    cid, ids = conv
    r = branch(api, cid, ids[1])
    assert r.status_code == 200, r.text
    new = r.json()["id"]
    assert _ids(ctx, cid) == ids[:2] and _ids(ctx, new) == ids[2:]
    th = conversations.thread(ctx, new)
    assert th["conversation"]["branched_from"] == {"conversation_id": cid, "title": "Trip plan", "task_id": ids[1],
                                                   "goal": "q1"}
    assert "Trip plan" in th["conversation"]["title"]


def test_cutting_after_the_last_exchange_starts_an_empty_branch(ctx, api, conv):
    cid, ids = conv
    new = branch(api, cid, ids[3]).json()["id"]
    assert _ids(ctx, cid) == ids and _ids(ctx, new) == []


def test_a_follow_up_in_a_branch_sees_the_exchanges_before_the_cut_and_not_the_ones_after(ctx, api, conv):
    cid, ids = conv
    new = branch(api, cid, ids[1]).json()["id"]
    later = _say(ctx, cid, "only in the original")
    mine = _say(ctx, new, "branch question", answer=False)
    text, n = conversations.history(ctx.conn, mine)
    assert n == 4 and [w in text for w in ("q0", "q1", "q2", "q3")] == [True] * 4
    assert "only in the original" not in text
    assert text.index("q1") < text.index("q2")
    text, _ = conversations.history(ctx.conn, later)
    assert "q2" not in text and "q1" in text


def test_history_is_still_capped_and_survives_a_branch_loop(ctx, api, conv):
    cid, ids = conv
    new = branch(api, cid, ids[0]).json()["id"]
    ctx.conn.execute("UPDATE conversations SET branch_after=? WHERE id=?", (ids[3], cid))
    extra = [_say(ctx, new, f"n{i}") for i in range(4)]
    text, n = conversations.history(ctx.conn, extra[-1])
    assert n == conversations.HISTORY_EXCHANGES and "q0" not in text
    text, n = conversations.history(ctx.conn, ids[1])
    assert n == 1 and "q0" in text


def test_what_a_task_already_saw_is_not_rewritten_by_a_later_cut(ctx, api, conv):
    cid, ids = conv
    mine = _say(ctx, cid, "follow up", answer=False)
    aid, before = ctx._conversation_history(mine)
    branch(api, cid, ids[0])
    assert ctx._conversation_history(mine) == (aid, before)


def test_merging_puts_both_threads_in_one_by_time_and_removes_the_other(ctx, api, conv):
    cid, ids = conv
    other = conversations.create(ctx.conn, "Side quest")["id"]
    side = [_say(ctx, other, "s0"), _say(ctx, other, "s1")]
    last = _say(ctx, cid, "q4")
    r = merge(api, cid, other)
    assert r.status_code == 200, r.text
    assert _ids(ctx, cid) == ids + side + [last]
    assert ctx.conn.execute("SELECT COUNT(*) FROM conversations WHERE id=?", (other,)).fetchone()[0] == 0
    positions = [p for (p,) in ctx.conn.execute(
        "SELECT position FROM conversation_tasks WHERE conversation_id=? ORDER BY position", (cid,))]
    assert positions == list(range(7))


def test_merging_a_branch_back_undoes_the_cut(ctx, api, conv):
    cid, ids = conv
    new = branch(api, cid, ids[1]).json()["id"]
    assert merge(api, cid, new).status_code == 200
    th = conversations.thread(ctx, cid)
    assert [i["task_id"] for i in th["items"]] == ids and th["conversation"]["branched_from"] is None


def test_merging_the_parent_into_its_branch_leaves_no_self_reference(ctx, api, conv):
    cid, ids = conv
    new = branch(api, cid, ids[1]).json()["id"]
    assert merge(api, new, cid).status_code == 200
    th = conversations.thread(ctx, new)
    assert [i["task_id"] for i in th["items"]] == ids and th["conversation"]["branched_from"] is None


def test_a_branch_of_a_merged_conversation_follows_its_task(ctx, api, conv):
    cid, ids = conv
    child = branch(api, cid, ids[3]).json()["id"]
    other = conversations.create(ctx.conn, "Keeper")["id"]
    _say(ctx, other, "k0")
    assert merge(api, other, cid).status_code == 200
    assert conversations.thread(ctx, child)["conversation"]["branched_from"]["conversation_id"] == other


def test_refusals(ctx, api, conv):
    cid, ids = conv
    other = conversations.create(ctx.conn, "Other")["id"]
    foreign = _say(ctx, other, "x")
    assert branch(api, cid, foreign).status_code == 404
    assert branch(api, "c_nothing", ids[0]).status_code == 404
    assert merge(api, cid, cid).status_code == 400
    assert merge(api, cid, "c_nothing").status_code == 404
    assert merge(api, "c_nothing", cid).status_code == 404
    assert _ids(ctx, cid) == ids


def test_cutting_and_merging_leave_the_integrity_record_alone(ctx, api, conv):
    cid, ids = conv
    new = branch(api, cid, ids[1]).json()["id"]
    merge(api, cid, new)
    assert critical(verify_all(ctx.conn, ctx.log, ctx.anchor)) == []


def test_deleting_the_task_a_branch_started_from_leaves_a_plain_conversation(ctx, api, conv):
    cid, ids = conv
    new = branch(api, cid, ids[3]).json()["id"]
    mine = _say(ctx, new, "after", answer=False)
    assert api.post(f"/conversations/{cid}/delete", json={"phrase": "delete"}).status_code == 200
    assert conversations.thread(ctx, new)["conversation"]["branched_from"] is None
    assert conversations.history(ctx.conn, mine) == ("", 0)


def test_the_listing_and_the_overview_name_the_parent(ctx, api, conv):
    cid, ids = conv
    new = branch(api, cid, ids[1]).json()["id"]
    row = next(c for c in api.get("/conversations").json()["conversations"] if c["id"] == new)
    assert row["branch_of"] == cid
    ov = api.get("/graph/overview").json()
    assert next(c for c in ov["conversations"] if c["id"] == new)["branch_after"] == ids[1]


def test_the_page_offers_both_through_their_routes():
    assert {("POST", "/conversations/{}/branch"), ("POST", "/conversations/{}/merge")} <= _calls(UI)
    assert "branchConversation" in UI and "mergeConversation" in UI


def test_the_map_draws_a_branch_from_the_exchange_it_left():
    import json
    import shutil
    import subprocess

    from test_console_overview import MAP_SRC

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = MAP_SRC + """
const data = {conversations: [{id: "c1", title: "One", tasks: ["a"], branch_after: null},
                              {id: "c2", title: "Two", tasks: ["b"], branch_after: "a"},
                              {id: "c3", title: "Gone", tasks: [], branch_after: "zzz"}],
              tasks: [{id: "a", goal: "A", status: "done", parent_id: null, conversation_id: "c1", steps: 1},
                      {id: "b", goal: "B", status: "done", parent_id: null, conversation_id: "c2", steps: 1}]};
process.stdout.write(JSON.stringify(mapLayout(data, new Map())));"""
    out = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
    branch_links = [(lk["source"], lk["target"]) for lk in out["links"] if lk.get("kind") == "branch"]
    assert branch_links == [("t:a", "s:c2")]
    nodes = {n["id"]: n for n in out["nodes"]}
    assert nodes["s:c2"]["conv"] == "c2" and nodes["t:b"]["conv"] == "c2"


def test_the_controls_are_where_sessions_live():
    sessions = UI[UI.index("async sessions()"):]
    sessions = sessions[:sessions.index("\n  async ")]
    assert "pickMerge" in sessions and "mergeConversation" in sessions
    item = UI[UI.index("function renderItem(it)"):UI.index("function stateCard(it)")]
    assert "branchConversation" in item
    click = UI[UI.index("function mapClick(n)"):UI.index("function closeMapInfo()")]
    assert "branchConversation" in click and "mergeConversation" in click
    assert "branched_from" in UI and "branch_of" in UI
