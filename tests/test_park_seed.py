"""D28 - a resumed task sees what it saw when it parked.

Measured 2026-09-15: a resumed task received its objective, its plan and the
released decisions - and nothing it had observed. No tool could recover the
rest: `search_history` does not index tool output, and `expand_artifact`
needs ids that only the lost tail carried.

Now the tail is written at the park as STRUCTURED entries, each with its own
taint, restored on resume, and followed by the id of every earlier
observation. The step counter is deliberately NOT restored: receipt keys are
built from it, and execute-once works as designed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.execute.loop import run_task
from agent.kernel.integrity import IntegrityError
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract, Taint

GOAL = "summarise the zephyrine ledger, then write a file"
SECRET = "OBSERVED_BEFORE_THE_PARK_quokka"
READ = '<action tool="read_file">' + json.dumps({"path": "work/notes.txt"}) + "</action>"
WRITE = ('<action tool="write_file">'
         + json.dumps({"path": "work/x.txt", "content": "hello"}) + "</action>")
FINAL = '<final>{"answer": "done"}</final>'


@pytest.fixture
def hctx(ctx):
    """A human in the loop: no deputy, no scoped auto-approval."""
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = False
    ctx.config.autonomy.plan_required = False
    return ctx


def _park(ctx):
    work = Path(ctx.config.root) / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "notes.txt").write_text(SECRET, encoding="utf-8")
    t = create_task(ctx.conn, ctx.log,
                    TaskContract(goal=GOAL, artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=600))
    ctx.backends["cloud"].queue(READ, WRITE)
    r = run_task(ctx, t.id)
    assert r.status == "paused", r
    return t, r.approval_id


def _resume(ctx, t, approval_id, new_process=True):
    """Resume after a human approves. `new_process` drops the in-memory taint
    tracker, so it is rebuilt from the record as a restarted process would."""
    ctx.resolve_approval(approval_id, True)
    if new_process:
        ctx._taint.pop(t.id, None)  # noqa: SLF001
    n = len(ctx.backends["cloud"].prompts)
    ctx.backends["cloud"].queue(FINAL)
    r = run_task(ctx, t.id)
    return r, ctx.backends["cloud"].prompts[n]


def _events(ctx, task_id, etype):
    return [json.loads(p) for (p,) in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq",
        (task_id, etype))]


def test_a_resumed_task_sees_what_it_observed_before_the_park(hctx):
    t, aid = _park(hctx)
    r, prompt = _resume(hctx, t, aid)
    assert r.status == "done", r
    assert SECRET in prompt, "the resumed model was not shown its own observation"
    (seed,) = _events(hctx, t.id, "task.park_seed")
    (restored,) = _events(hctx, t.id, "task.seed_restored")
    assert restored["artifact"] == seed["artifact"] and restored["entries"] >= 1


def test_every_earlier_observation_is_listed_by_id_and_readable(hctx):
    from agent.execute.tools import tool_expand_artifact

    t, aid = _park(hctx)
    _, prompt = _resume(hctx, t, aid)
    first = _events(hctx, t.id, "artifact.written")[0]["id"]
    assert first in prompt, "the id of an earlier observation is not in the resumed tail"
    got = tool_expand_artifact(hctx, t, id=first)
    assert SECRET in json.dumps(got), got


def test_the_restore_does_not_make_the_objective_untrusted(hctx):
    """Per-entry taint IS the design. A seed restored as one blob carries the
    join of everything in it - UNTRUSTED - and the objective's own words would
    then park every action that quotes them: on a channel task, a human
    interrupted for no security gain."""
    t, aid = _park(hctx)
    _resume(hctx, t, aid)
    tracker = hctx.taint_tracker(t.id)
    assert tracker.taint_of("zephyrine") != Taint.UNTRUSTED, (
        "the objective became untrusted on resume")
    assert tracker.taint_of(SECRET) == Taint.UNTRUSTED, (
        "an untrusted observation lost its taint on resume")


def test_the_step_counter_is_not_restored(hctx):
    """Receipt keys are (task, step, tool, args). Restoring step numbering
    would change every later key - its own decision, not this one's."""
    t, aid = _park(hctx)
    _resume(hctx, t, aid)
    steps = [e["step"] for e in _events(hctx, t.id, "model.called")]
    assert steps == [0, 1, 0], steps


def test_a_tampered_seed_halts_the_resume(hctx):
    t, aid = _park(hctx)
    hctx.conn.execute("UPDATE artifacts SET content = content || ' ' "
                      "WHERE task_id=? AND kind='park_seed'", (t.id,))
    hctx.conn.commit()
    hctx.resolve_approval(aid, True)
    hctx.backends["cloud"].queue(FINAL)
    with pytest.raises(IntegrityError, match="park seed"):
        run_task(hctx, t.id)
