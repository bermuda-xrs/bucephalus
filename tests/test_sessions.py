"""B1 - lineage, the memory flush, and the boundary between sessions.

The boundary sits at the deletion-compaction point that already existed, and
compaction itself is unchanged (9.1). What is new: one flush call before
anything is deleted, the session row closed and a child opened with a FRESH
tier-1 snapshot, and the prefix rebuilt from it. No summarizer - the Phase 9
design STOP explains why.

Each test was run against the code without the behaviour it checks and goes
red there.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

import agent.execute.loop as loop
from agent.context import AgentContext
from agent.execute.loop import run_task
from agent.execute.tools import tool_expand_artifact, tool_search_history
from agent.kernel import sessions
from agent.kernel.search import search
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, Taint, TaskContract
from agent.ledger.artifacts import write_artifact
from agent.memory.store import MemoryStore, record_fingerprint

FINAL = '<final>{"answer": "done"}</final>'
FACT = "The deploy port for this service is 8443."
SECRET = "The admin password is hunter2-zz81."


def READ(name):
    return ('<action tool="read_file">'
            + json.dumps({"path": f"work/{name}"}) + "</action>")


def REMEMBER(text):
    return ('<action tool="memory_write">'
            + json.dumps({"text": text}) + "</action>")


def _mk(ctx, goal):
    return create_task(ctx.conn, ctx.log,
                       TaskContract(goal=goal, artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=600))


@pytest.fixture
def sctx(ctx, tmp_path):
    ctx.memory = MemoryStore(tmp_path / "memory")   # never the user's memory
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.backends["cloud"].cfg.usable_context_window = 3000   # compact keeps ~600
    work = Path(ctx.config.root) / "work"
    for name in "abcde":
        body = f"File {name}. " + " ".join(
            f"{name}-{k} value {k * 7}" for k in range(90))
        if name == "a":
            body += " " + SECRET
        (work / f"{name}.txt").write_text(body, encoding="utf-8")
    return ctx


@pytest.fixture
def boundary(monkeypatch):
    """Fire the compaction boundary exactly once, on the Nth step."""
    def at(n):
        seen = {"k": 0}

        def fake(prefix_tokens, tail_tokens, window, ratio):
            seen["k"] += 1
            return "compact" if seen["k"] == n else "ok"
        monkeypatch.setattr(loop, "enforce_ceiling", fake)
    return at


def _run(ctx, task, flush_reply, boundary, isolated=False):
    if isolated:
        ctx.config.isolate_retrieval = True
    boundary(5)
    script = [READ("a.txt"), READ("b.txt"), READ("c.txt"), READ("d.txt")]
    if flush_reply is not None:
        script.append(flush_reply)
    ctx.backends["cloud"].queue(*script, READ("e.txt"), FINAL)
    return run_task(ctx, task.id)


def _calls(ctx, task):
    return ctx.conn.execute(
        """SELECT purpose, cache_read_tokens, cache_write_tokens FROM calls
           WHERE task_id=? ORDER BY id""", (task.id,)).fetchall()


def _events(ctx, task, etype):
    return [json.loads(p) for (p,) in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq",
        (task.id, etype))]


# -- 9.3: the boundary, the chain, the lineage -----------------------------

def test_a_long_task_crosses_a_session_boundary_with_the_chain_intact(
        sctx, task, boundary):
    r = _run(sctx, task, "NOTHING", boundary)
    assert r.status == "done"

    first, second = sessions.for_task(sctx.conn, task.id)
    assert first["status"] == "closed" and second["status"] == "closed"
    assert second["parent_id"] == first["id"]
    assert second["opened_reason"] == "boundary"
    assert second["seed_artifact"], "what seeded the child is recorded"
    assert sessions.lineage(sctx.conn, second["id"]) == [first["id"], second["id"]]
    assert [c["reason"] for c in _events(sctx, task, "session.closed")] == \
        ["boundary", "done"]
    assert sctx.log.verify(task.id)[0] is True
    assert "dropped" in sctx.backends["cloud"].prompts[5], (
        "the child's tail starts from what compaction left")


def test_one_open_session_per_task(ctx):
    a = _mk(ctx, "attempt")
    sessions.open_session(ctx.conn, ctx.log, a.id, {})
    with pytest.raises(sqlite3.IntegrityError):
        sessions.open_session(ctx.conn, ctx.log, a.id, {})


# -- 9.4: the flush --------------------------------------------------------

def test_a_fact_flushed_before_compaction_is_in_the_next_prefix(
        sctx, task, boundary):
    assert _run(sctx, task, REMEMBER(FACT), boundary).status == "done", (
        "the prefix changed at the boundary; a PrefixGuard not reset there "
        "crashes the step AFTER the one this test inspects")
    calls = sctx.backends["cloud"].calls
    assert FACT not in calls[3]["prefix"], "not before the boundary"
    assert FACT not in calls[4]["prefix"], "not in the flush call itself"
    assert FACT in calls[5]["prefix"], "the rebuilt prefix carries it"
    assert FACT in sctx.memory.read_raw("memory")
    child = _events(sctx, task, "session.opened")[-1]
    assert child["flush"] == "written" and child["memory_changed"] is True


def test_a_flush_blocked_by_taint_is_visible_not_silent(sctx, task, boundary):
    """The fact is copied verbatim from a file the task read - untrusted - so
    tier 1 refuses it. Refused is correct. Silently refused is a fact that
    vanished at compression with no record of why."""
    _run(sctx, task, REMEMBER(SECRET), boundary)

    refused = _events(sctx, task, "memory.flush_refused")
    assert refused and "untrusted" in refused[0]["reason"].lower()
    assert "zz81" not in sctx.memory.read_raw("memory")
    assert "REFUSED" in sctx.backends["cloud"].prompts[5], (
        "the model is told, in the next session's tail")
    child = _events(sctx, task, "session.opened")[-1]
    assert child["flush"] == "refused" and child["memory_changed"] is False


def test_the_flush_is_skipped_visibly_under_isolation(sctx, task, boundary):
    _run(sctx, task, None, boundary, isolated=True)
    assert "memory_flush" not in [c[0] for c in _calls(sctx, task)]
    child = _events(sctx, task, "session.opened")[-1]
    assert child["flush"] == "skipped" and "isolation" in child["why"]


# -- 9.6 offline: the block cache, P5 of the committed prediction ----------

def test_a_changed_prefix_is_a_full_miss_at_the_boundary(sctx, task, boundary):
    _run(sctx, task, REMEMBER(FACT), boundary)
    rows = _calls(sctx, task)
    assert [p for p, _, _ in rows][4] == "memory_flush"
    flush, first_after = rows[4], rows[5]
    assert flush[1] > 0, "the flush reuses the unchanged prefix"
    assert first_after[1] == 0 and first_after[2] > 0, (
        "P5: the prefix changed at the boundary, so a block cache misses")


def test_an_unchanged_prefix_is_a_full_hit_at_the_boundary(sctx, task, boundary):
    """The control. A rebuild that changes no bytes must NOT miss: a miss here
    would mean the rebuild changed prefix bytes it had no reason to change."""
    _run(sctx, task, "NOTHING", boundary)
    first_after = _calls(sctx, task)[5]
    assert first_after[1] > 0 and first_after[2] == 0


# -- DR1 lifetime: the snapshot belongs to the session ----------------------

def test_a_resumed_task_keeps_its_sessions_memory_snapshot(sctx, task):
    sctx.memory.write("memory", "fact one", Taint.USER)
    record_fingerprint(sctx.memory)
    sctx.config.autonomy.approve_scoped_writes = False
    sctx.backends["cloud"].queue(
        '<action tool="write_file">'
        + json.dumps({"path": "work/x.txt", "content": "hi"}) + "</action>")
    assert run_task(sctx, task.id).status == "paused"

    sctx.memory.write("memory", "fact two, written after the task started",
                      Taint.USER)
    record_fingerprint(sctx.memory)

    second = AgentContext(sctx.config)
    second.memory = MemoryStore(sctx.memory.root)
    try:
        snap = json.dumps(second.freeze_memory(task.id))
        assert "fact one" in snap
        assert "written after the task started" not in snap, (
            "a resumed task re-read tier 1 as it stands now: a deferred write "
            "became visible early, and only because the process restarted")
    finally:
        second.close()


# -- 9.7: a boundary never crosses an attempt --------------------------------

def test_a_session_cannot_descend_from_another_attempt(ctx):
    a, b = _mk(ctx, "attempt A"), _mk(ctx, "attempt B")
    sa = sessions.open_session(ctx.conn, ctx.log, a.id, {})
    with pytest.raises(sqlite3.DatabaseError, match="same task"):
        sessions.open_session(ctx.conn, ctx.log, b.id, {}, parent_id=sa,
                              reason="boundary")


def test_what_a_boundary_left_cannot_be_read_from_another_attempt(
        sctx, task, boundary):
    _run(sctx, task, None, boundary, isolated=True)
    rows = sessions.for_task(sctx.conn, task.id)
    seed = rows[-1]["seed_artifact"]
    assert seed

    other = _mk(sctx, "another attempt")
    assert tool_expand_artifact(sctx, other, seed)["ok"] is False
    found = tool_search_history(sctx, other, "value")
    assert all(r.get("ref") != seed for r in found.get("results", []))
    owners = {sctx.conn.execute("SELECT task_id FROM sessions WHERE id=?",
                                (s,)).fetchone()[0]
              for s in sessions.lineage(sctx.conn, rows[-1]["id"])}
    assert owners == {task.id}


# -- 9.5, as it applies to this architecture --------------------------------

def test_every_reference_a_boundary_leaves_expands_within_its_attempt(
        sctx, task, boundary):
    """Calls never enter this loop's tail - only their results do - so a
    compaction cannot split a call from its result. What compaction CAN leave
    behind is a reference: a tombstone pointing at an artifact. Each one must
    expand for the attempt that holds it, under isolation included."""
    _run(sctx, task, None, boundary, isolated=True)
    after = sctx.backends["cloud"].prompts[4]     # isolated: no flush call
    refs = re.findall(r'expand_artifact\("([^"]+)"\)', after)
    assert refs, "the boundary left references"
    for aid in refs:
        assert tool_expand_artifact(sctx, task, aid)["ok"] is True, aid


# -- 9.3: what search reports as a session ---------------------------------

def test_search_names_a_hit_by_its_lineage_not_a_rotated_session(
        sctx, task, boundary):
    _run(sctx, task, "NOTHING", boundary)
    write_artifact(sctx.conn, task.id, "answer", "a phrase with quokka in it",
                   Taint.TRUSTED, {})
    root = sessions.for_task(sctx.conn, task.id)[0]["id"]
    assert [h.session_id for h in search(sctx.conn, "quokka")] == [root]

    legacy = _mk(sctx, "never ran")
    write_artifact(sctx.conn, legacy.id, "answer", "another phrase, wombat",
                   Taint.TRUSTED, {})
    assert [h.session_id for h in search(sctx.conn, "wombat")] == [legacy.id], (
        "a task with no session rows keeps the name it always had")


def test_a_flush_that_would_need_a_human_is_refused_not_parked(
        sctx, task, boundary):
    """A boundary is not a place to wait. A parked flush would park the task in
    the middle of compacting it; the flush is one chance, not a conversation."""
    sctx.config.autonomy.approve_scoped_writes = False   # memory_write needs a human
    r = _run(sctx, task, REMEMBER(FACT), boundary)
    assert r.status == "done", "the task stopped at its own boundary"
    refused = _events(sctx, task, "memory.flush_refused")
    assert refused and "cannot wait" in refused[0]["reason"]
    assert FACT not in sctx.memory.read_raw("memory")
    assert sctx.pending_approvals(task.id) == [], "nothing was left parked"
