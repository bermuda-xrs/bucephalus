"""The contamination attack, written the way the injection test is written.

THE ATTACK, stated as it would appear in a published result:

    control(E03) runs, writes an answer, the trigger indexes it
    skill(E03)   runs, calls search_history, finds control's answer
    -> a discordant pair produced by RETRIEVAL, not by the skill

The gate's entire output is b and c counts. One successful cross-arm
retrieval corrupts both, the run reports a lift, and nothing in the record
says the skill arm looked the answer up.

This is the grader-leak class one level up. `workspace.py` fixes the disk
version — two arms sharing a workspace, so the control arm gets graded
against the answer the skill arm just wrote. This file is the database
version, and it covers all three paths a benchmark arm could use:

    1. search_history        — the index holds `answer` artifacts
    2. expand_artifact       — an artifact id survives the workspace reset
    3. the same task, rerun  — run 2 reading run 1 breaks the noise floor

Path 3 is the one a per-ARM namespace would have missed. The noise-floor
classification (3/3, 0/3, flaky) assumes the runs are independent draws, so
scoping has to be per ATTEMPT, which is also what workspace.py already does.
"""

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
    """A control arm that answered correctly, and a skill arm that has not."""
    make_attempt(conn, "t_control", "Compute the escape velocity of Titan")
    write_answer(conn, "a_control", "t_control")
    make_attempt(conn, "t_skill", "Compute the escape velocity of Titan")
    return conn


# --------------------------------------------------------------------------
# The attack
# --------------------------------------------------------------------------

def test_the_skill_arm_cannot_search_out_the_control_arms_answer(paired):
    ctx = Ctx(paired, isolate=True)
    out = tool_search_history(ctx, Task("t_skill"), "escape velocity Titan")
    assert out["ok"] is True
    # Its OWN contract is legitimately in scope - the arm may read the goal it
    # was given. What must never appear is anything belonging to the other arm.
    assert all(r["task_id"] == "t_skill" for r in out["results"]), (
        "the skill arm retrieved the control arm's work; every discordant "
        f"pair in this run would be uninterpretable: {out['results']}")
    assert ANSWER not in json.dumps(out)


def test_the_skill_arm_cannot_expand_the_control_arms_artifact(paired):
    """Path 2. An artifact id is a content hash, but search returns ids in its
    `ref` field and an id survives the workspace reset, which wipes files and
    not rows. Scoping the search alone would leave this open."""
    ctx = Ctx(paired, isolate=True)
    out = tool_expand_artifact(ctx, Task("t_skill"), "a_control")
    assert out["ok"] is False
    assert "another task attempt" in out["error"]
    assert ANSWER not in json.dumps(out)


def test_a_rerun_of_the_same_task_cannot_read_the_previous_run(conn):
    """Path 3, and the reason scoping is per ATTEMPT rather than per ARM.

    A per-arm namespace leaves run 2 of the control arm able to read run 1 of
    the control arm. The noise floor classifies a task 3/3, 0/3 or flaky on
    the assumption those runs are independent draws; they are not if run 2 can
    look up run 1.
    """
    make_attempt(conn, "t_run1", "Compute the escape velocity of Titan")
    write_answer(conn, "a_run1", "t_run1")
    make_attempt(conn, "t_run2", "Compute the escape velocity of Titan")

    ctx = Ctx(conn, isolate=True)
    out = tool_search_history(ctx, Task("t_run2"), "escape velocity")
    assert all(r["task_id"] == "t_run2" for r in out["results"]), out["results"]
    assert ANSWER not in json.dumps(out)
    assert tool_expand_artifact(ctx, Task("t_run2"), "a_run1")["ok"] is False


def test_an_arm_can_still_read_its_own_work(paired):
    """The isolation has to leave the tool usable, or it is just removal by
    another name — and removal would change the tool manual, which is rendered
    into the cache-stable prefix."""
    ctx = Ctx(paired, isolate=True)
    write_answer(paired, "a_skill_own", "t_skill", "my own working note")

    out = tool_search_history(ctx, Task("t_skill"), "working note")
    assert out["ok"] and out["results"], "an arm must see its own artifacts"
    assert all(r["task_id"] == "t_skill" for r in out["results"])
    assert tool_expand_artifact(ctx, Task("t_skill"), "a_skill_own")["ok"]


def test_a_delegated_child_shares_its_parents_scope(conn):
    """A subtask is part of the same attempt, so it reads what its parent can
    and nothing more."""
    make_attempt(conn, "t_root", "parent goal")
    make_attempt(conn, "t_child", "delegated goal", parent="t_root")
    write_answer(conn, "a_root", "t_root", "parent produced this")
    make_attempt(conn, "t_other", "a different attempt")
    write_answer(conn, "a_other", "t_other", "other attempt produced this")

    ctx = Ctx(conn, isolate=True)
    assert task_tree(conn, "t_child") == {"t_root", "t_child"}
    assert tool_expand_artifact(ctx, Task("t_child"), "a_root")["ok"]
    assert tool_expand_artifact(ctx, Task("t_child"), "a_other")["ok"] is False


# --------------------------------------------------------------------------
# The default, and the record
# --------------------------------------------------------------------------

def test_normal_use_is_unrestricted(paired):
    """Searching earlier sessions is the entire point of the feature. The fix
    must not quietly delete it outside benchmarks."""
    ctx = Ctx(paired, isolate=False)
    out = tool_search_history(ctx, Task("t_skill"), "escape velocity Titan")
    assert out["results"], "unrestricted retrieval is correct for normal use"
    assert tool_expand_artifact(ctx, Task("t_skill"), "a_control")["ok"]


def test_isolation_with_no_task_refuses_rather_than_opening_up(conn):
    """Fail closed. An isolation rule that becomes unrestricted when it cannot
    identify the caller is not an isolation rule."""
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
    """A contaminated measurement caused by editing the wrong config file is
    the failure this exists to remove, so it must not be a yaml key."""
    src = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    assert "ctx.config.isolate_retrieval = True" in src
    assert '"retrieval_isolation": _isolation_state(ctx)' in src
    for cfg in ("config.yaml", "config.local.yaml", "config.openai.yaml"):
        assert "isolate_retrieval" not in (ROOT / cfg).read_text(encoding="utf-8")


def test_scoping_does_not_shorten_an_in_scope_result_set(conn):
    """The filter is applied in the query, not to the results. Filtering
    afterwards would let an out-of-scope row consume a LIMIT slot and silently
    return fewer answers than exist — a scope check that changes what an
    in-scope search returns is a second bug."""
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
    """D23. Artifacts are content-addressed with INSERT OR IGNORE, so two arms
    producing identical bytes share ONE row that keeps the FIRST writer's
    task_id. Asking who owns the row answers a different question from who
    produced it.

    Not hypothetical: both arms run `list_dir` against an identically-reset
    workspace and get byte-identical output. The skill arm was refused access
    to its own artifact — arm ASYMMETRY created by the rule meant to prevent
    cross-arm contamination, which is its own way of making two runs
    incomparable.
    """
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

    # Authorship comes from the chain, which records it per task.
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

