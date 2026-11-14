from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.kernel import search as search_mod  # noqa: E402
from agent.kernel.db import open_db  # noqa: E402
from agent.kernel.types import Taint  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    c = open_db(tmp_path / "s.db")
    yield c
    c.close()


class _Cfg:
    isolate_retrieval = False


def _ctx(conn):
    class Ctx:
        pass
    c = Ctx()
    c.conn = conn
    c.config = _Cfg()
    return c


def add_task(conn, tid, goal, parent=None, criterion="expected_value"):
    import json
    import time
    conn.execute(
        """INSERT INTO tasks(id, parent_id, contract_json, status, budget_json,
                             created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (tid, parent,
         json.dumps({"goal": goal, "success_criterion": criterion,
                     "artifact_scope": ["work"]}),
         "done", "{}", time.time(), time.time()))
    conn.commit()


def add_artifact(conn, aid, tid, kind, content, taint="trusted"):
    import time
    conn.execute(
        """INSERT INTO artifacts(id, task_id, kind, content, taint,
                                 origin_json, n_tokens, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (aid, tid, kind, content, taint, "{}", len(content) // 4, time.time()))
    conn.commit()


def test_search_finds_content_from_a_prior_session(conn):
    add_task(conn, "t_old", "Calculate the escape velocity of Titan")
    add_artifact(conn, "a1", "t_old", "answer", "escape velocity is 2639 m/s")
    add_task(conn, "t_new", "Something completely unrelated about pastry")

    hits = search_mod.search(conn, "escape velocity")
    assert hits, "a goal and an answer from an earlier task must be findable"
    assert {h.task_id for h in hits} == {"t_old"}
    assert any("escape" in h.snippet.lower() for h in hits)


def test_results_carry_untrusted_even_when_the_artifact_is_trusted(conn):
    add_task(conn, "t1", "goal about widgets")
    add_artifact(conn, "a1", "t1", "answer", "widgets are made of tungsten",
                 taint="trusted")
    row = conn.execute("SELECT taint FROM artifacts WHERE id='a1'").fetchone()
    assert row[0] == "trusted", "fixture must actually be a trusted artifact"

    hits = search_mod.search(conn, "tungsten")
    assert hits
    assert all(h.taint is Taint.UNTRUSTED for h in hits)
    assert all(h.as_dict()["taint"] == "untrusted" for h in hits)


def test_the_chain_still_verifies_after_indexing(conn):
    from agent.kernel.events import EventLog

    add_task(conn, "t1", "a goal worth indexing")
    log = EventLog(conn)
    for i in range(5):
        log.append("t1", "task.budget_spent", {"i": i})
    head_before = log.head("t1")
    assert log.verify("t1") == (True, None)

    add_artifact(conn, "a1", "t1", "answer", "indexed content here")
    search_mod.rebuild(conn)
    assert search_mod.search(conn, "indexed")

    assert log.verify("t1") == (True, None), (
        "building or rebuilding the index must not disturb the chain")
    assert log.head("t1") == head_before, (
        "indexing must not append to the event log at all")


def test_a_rebuild_reproduces_the_index_exactly(conn):
    add_task(conn, "t1", "first goal about turbines")
    add_task(conn, "t2", "second goal about gearboxes", parent="t1")
    add_artifact(conn, "a1", "t1", "answer", "turbine answer")
    add_artifact(conn, "a2", "t2", "plan", "gearbox plan")

    def snapshot():
        return sorted(conn.execute(
            "SELECT body, source, ref, task_id, kind FROM search_index"))

    built_by_triggers = snapshot()
    assert built_by_triggers, "the triggers must have populated it"

    search_mod.rebuild(conn)
    assert snapshot() == built_by_triggers


def test_tool_output_is_never_indexed(conn):
    add_task(conn, "t1", "a goal")
    add_artifact(conn, "a1", "t1", "tool_output",
                 "drwxr-xr-x listing noise everywhere", taint="untrusted")
    add_artifact(conn, "a2", "t1", "answer", "the real answer")

    assert not search_mod.search(conn, "listing")
    assert search_mod.search(conn, "real")

    search_mod.rebuild(conn)
    assert not search_mod.search(conn, "listing"), (
        "the rebuild path must exclude the same kinds as the triggers")
    v = search_mod.verify(conn)
    assert v["consistent"] and v["excluded_tool_outputs"] == 1


def test_results_are_bounded_and_paginated(conn):
    add_task(conn, "t1", "a goal")
    for i in range(40):
        add_artifact(conn, f"a{i}", "t1", "answer", f"repeated marker text {i}")

    assert len(search_mod.search(conn, "marker")) == search_mod.DEFAULT_LIMIT
    assert len(search_mod.search(conn, "marker", limit=1000)) == search_mod.MAX_LIMIT
    first = search_mod.search(conn, "marker", limit=3)
    second = search_mod.search(conn, "marker", limit=3, offset=3)
    assert {h.ref for h in first}.isdisjoint({h.ref for h in second})


def test_a_snippet_never_returns_a_whole_artifact(conn):
    add_task(conn, "t1", "a goal")
    add_artifact(conn, "a1", "t1", "answer", "needle " + ("filler " * 5000))
    hit = search_mod.search(conn, "needle")[0]
    assert len(hit.snippet) <= search_mod.MAX_BODY_CHARS


def test_a_subtask_reports_its_root_as_the_session(conn):
    add_task(conn, "root", "the parent goal")
    add_task(conn, "child", "delegated subgoal about flanges", parent="root")
    add_task(conn, "grand", "deeper subgoal about flanges", parent="child")

    hits = search_mod.search(conn, "flanges")
    assert hits
    assert {h.session_id for h in hits} == {"root"}
    assert {h.task_id for h in hits} == {"child", "grand"}


def test_a_malformed_query_is_a_bad_argument_not_a_crash(conn):
    add_task(conn, "t1", "a goal")
    with pytest.raises(ValueError, match="malformed search query"):
        search_mod.search(conn, 'unbalanced "quote')


def test_the_tool_returns_the_error_rather_than_raising(conn):
    from agent.execute.tools import tool_search_history

    ctx = _ctx(conn)
    add_task(conn, "t1", "a goal")

    out = tool_search_history(ctx, None, 'unbalanced "quote')
    assert out["ok"] is False and "malformed" in out["error"]


def test_the_tool_marks_every_result_untrusted(conn):
    from agent.execute.tools import tool_search_history

    ctx = _ctx(conn)
    add_task(conn, "t1", "a goal about magnets")
    add_artifact(conn, "a1", "t1", "answer", "magnets attract iron",
                 taint="trusted")

    out = tool_search_history(ctx, None, "magnets")
    assert out["ok"] and out["taint"] == "untrusted"
    assert out["results"] and all(r["taint"] == "untrusted"
                                 for r in out["results"])


def test_a_contract_hit_renders_the_goal_not_raw_json(conn):
    from agent.execute.tools import tool_search_history

    ctx = _ctx(conn)
    add_task(conn, "t1", "measure the coefficient of drag")

    out = tool_search_history(ctx, None, "coefficient")
    snip = out["results"][0]["snippet"]
    assert "measure the coefficient of drag" in snip
    assert "contract_json" not in snip and '{"goal"' not in snip


def test_the_registered_tool_is_read_only_and_untrusted():
    from agent.execute.tools import default_registry

    spec = default_registry().get("search_history")
    assert spec is not None
    assert spec.side_effecting is False
    assert spec.output_taint is Taint.UNTRUSTED
    assert spec.max_arg_taint is Taint.UNTRUSTED


def test_search_output_taint_reaches_the_tracker_by_the_shared_path():
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(
        encoding="utf-8")
    assert src.count("tool.output_taint") == 1, (
        "more than one taint-stamping site means a tool can be exempted")
    i = src.index("taint = tool.output_taint")
    end = src.index(chr(10) + "def ", i)
    window = src[i:end]
    assert "write_artifact" in window and "observe(" in window, (
        "the stamped taint must reach both the artifact and the tracker")


def test_a_rebuild_that_fails_keeps_the_old_index(conn):
    add_task(conn, "t1", "find the walrus ledger")
    add_artifact(conn, "a1", "t1", "answer", "the walrus ledger is in the attic")
    before = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]
    assert before == 2

    class FailsOnArtifacts:
        def __init__(self, c):
            self.c = c

        def execute(self, sql, *a):
            if "FROM artifacts" in sql:
                raise RuntimeError("disk full")
            return self.c.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self.c, name)

        def __enter__(self):
            return self.c.__enter__()

        def __exit__(self, *a):
            return self.c.__exit__(*a)

    with pytest.raises(RuntimeError):
        search_mod.rebuild(FailsOnArtifacts(conn))
    assert conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0] == before
