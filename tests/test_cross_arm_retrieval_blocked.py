from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.execute.tools import tool_expand_artifact, tool_search_history  # noqa: E402
from agent.kernel.db import open_db  # noqa: E402
from agent.kernel.scope import isolation_state, task_tree, visible_tasks  # noqa: E402

ANSWER = "the escape velocity of Titan is 2639 m/s"


class Cfg:
    def __init__(self, isolate):
        self.isolate_retrieval = isolate


class Ctx:
    def __init__(self, conn, isolate):
        self.conn = conn
        self.config = Cfg(isolate)


class Task:
    def __init__(self, tid):
        self.id = tid


@pytest.fixture()
def conn(tmp_path):
    c = open_db(tmp_path / "arms.db")
    yield c
    c.close()


def make_attempt(conn, tid, goal, parent=None):
    conn.execute(
        """INSERT INTO tasks(id, parent_id, contract_json, status, budget_json,
                             created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (tid, parent, json.dumps({"goal": goal}), "done", "{}",
         time.time(), time.time()))
    conn.commit()


def write_answer(conn, aid, tid, text=ANSWER):
    conn.execute(
        """INSERT INTO artifacts(id, task_id, kind, content, taint,
                                 origin_json, n_tokens, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (aid, tid, "answer", text, "trusted", "{}", 10, time.time()))
    conn.commit()


@pytest.fixture()
def paired(conn):
    make_attempt(conn, "t_control", "Compute the escape velocity of Titan")
    write_answer(conn, "a_control", "t_control")
    make_attempt(conn, "t_skill", "Compute the escape velocity of Titan")
    return conn


def test_the_skill_arm_cannot_search_out_the_control_arms_answer(paired):
    ctx = Ctx(paired, isolate=True)
    out = tool_search_history(ctx, Task("t_skill"), "escape velocity Titan")
    assert out["ok"] is True
    assert all(r["task_id"] == "t_skill" for r in out["results"]), (
        "the skill arm retrieved the control arm's work; every discordant "
        f"pair in this run would be uninterpretable: {out['results']}")
    assert ANSWER not in json.dumps(out)


def test_the_skill_arm_cannot_expand_the_control_arms_artifact(paired):
    ctx = Ctx(paired, isolate=True)
    out = tool_expand_artifact(ctx, Task("t_skill"), "a_control")
    assert out["ok"] is False
    assert "another task attempt" in out["error"]
    assert ANSWER not in json.dumps(out)


def test_a_rerun_of_the_same_task_cannot_read_the_previous_run(conn):
    make_attempt(conn, "t_run1", "Compute the escape velocity of Titan")
    write_answer(conn, "a_run1", "t_run1")
    make_attempt(conn, "t_run2", "Compute the escape velocity of Titan")

    ctx = Ctx(conn, isolate=True)
    out = tool_search_history(ctx, Task("t_run2"), "escape velocity")
    assert all(r["task_id"] == "t_run2" for r in out["results"]), out["results"]
    assert ANSWER not in json.dumps(out)
    assert tool_expand_artifact(ctx, Task("t_run2"), "a_run1")["ok"] is False


def test_an_arm_can_still_read_its_own_work(paired):
    ctx = Ctx(paired, isolate=True)
    write_answer(paired, "a_skill_own", "t_skill", "my own working note")

    out = tool_search_history(ctx, Task("t_skill"), "working note")
    assert out["ok"] and out["results"], "an arm must see its own artifacts"
    assert all(r["task_id"] == "t_skill" for r in out["results"])
    assert tool_expand_artifact(ctx, Task("t_skill"), "a_skill_own")["ok"]


def test_a_delegated_child_shares_its_parents_scope(conn):
    make_attempt(conn, "t_root", "parent goal")
    make_attempt(conn, "t_child", "delegated goal", parent="t_root")
    write_answer(conn, "a_root", "t_root", "parent produced this")
    make_attempt(conn, "t_other", "a different attempt")
    write_answer(conn, "a_other", "t_other", "other attempt produced this")

    ctx = Ctx(conn, isolate=True)
    assert task_tree(conn, "t_child") == {"t_root", "t_child"}
    assert tool_expand_artifact(ctx, Task("t_child"), "a_root")["ok"]
    assert tool_expand_artifact(ctx, Task("t_child"), "a_other")["ok"] is False


def test_normal_use_is_unrestricted(paired):
    ctx = Ctx(paired, isolate=False)
    out = tool_search_history(ctx, Task("t_skill"), "escape velocity Titan")
    assert out["results"], "unrestricted retrieval is correct for normal use"
    assert tool_expand_artifact(ctx, Task("t_skill"), "a_control")["ok"]


def test_isolation_with_no_task_refuses_rather_than_opening_up(conn):
    ctx = Ctx(conn, isolate=True)
    assert visible_tasks(ctx, None) == set()
    make_attempt(conn, "t1", "goal")
    write_answer(conn, "a1", "t1")
    assert tool_search_history(ctx, None, "escape")["results"] == []


def test_the_run_record_states_which_subsystems_were_active(conn):
    on = isolation_state(Ctx(conn, isolate=True))
    off = isolation_state(Ctx(conn, isolate=False))
    assert on["isolate_retrieval"] is True
    assert "scoped" in on["search_history"] and "scoped" in on["expand_artifact"]
    assert off["isolate_retrieval"] is False
    assert "INVALID for a paired trial" in off["note"], (
        "the record must say plainly that an unisolated run cannot be used "
        "for a paired trial, rather than leaving a reader to infer it")


def test_the_bench_turns_isolation_on_itself_not_from_yaml():
    src = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    assert "ctx.config.isolate_retrieval = True" in src
    assert '"retrieval_isolation": _isolation_state(ctx)' in src
    for cfg in ("config.yaml", "config.local.yaml", "config.openai.yaml"):
        assert "isolate_retrieval" not in (ROOT / cfg).read_text(encoding="utf-8")


def test_scoping_does_not_shorten_an_in_scope_result_set(conn):
    make_attempt(conn, "t_mine", "goal")
    make_attempt(conn, "t_theirs", "goal")
    for i in range(10):
        write_answer(conn, f"m{i}", "t_mine", f"marker mine {i}")
        write_answer(conn, f"o{i}", "t_theirs", f"marker theirs {i}")

    ctx = Ctx(conn, isolate=True)
    out = tool_search_history(ctx, Task("t_mine"), "marker", limit=5)
    assert len(out["results"]) == 5
    assert all(r["task_id"] == "t_mine" for r in out["results"])


def test_an_arm_can_expand_an_artifact_whose_bytes_another_arm_wrote_first(conn):
    import time as _t
    from agent.kernel.types import Taint
    from agent.ledger.artifacts import write_artifact

    make_attempt(conn, "t_first", "same goal")
    make_attempt(conn, "t_second", "same goal")
    SAME = chr(10).join(["work/", "output/", "input.json"])

    aid = write_artifact(conn, "t_first", "tool_output", SAME,
                         Taint.UNTRUSTED, {"tool": "list_dir"})
    again = write_artifact(conn, "t_second", "tool_output", SAME,
                           Taint.UNTRUSTED, {"tool": "list_dir"})
    assert aid == again, "content addressing means one row"

    for tid in ("t_first", "t_second"):
        conn.execute(
            "INSERT INTO events(task_id, seq, type, payload_json, prev_hash,"
            " hash, created_at) VALUES (?,?,?,?,?,?,?)",
            (tid, 99, "artifact.written", json.dumps({"id": aid,
                                                      "kind": "tool_output"}),
             "x", "y", _t.time()))
    conn.commit()

    ctx = Ctx(conn, isolate=True)
    assert tool_expand_artifact(ctx, Task("t_second"), aid)["ok"] is True, (
        "an arm must be able to expand output it produced, even when an "
        "earlier arm produced the same bytes first")

    make_attempt(conn, "t_stranger", "unrelated")
    assert tool_expand_artifact(ctx, Task("t_stranger"), aid)["ok"] is False, (
        "and a task that never wrote it must still be refused")
