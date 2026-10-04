"""D20 — a task's execution does not continue without its chain being verified.

`integrity.resume()` is M1.4/M1.5: verify the chain, halt on a break, compare
head against anchor. It was written, unit-tested, and IMPORTED BY NOTHING, so
a paused task resumed without any of it happening. Four modules import other
names from that file and never this one.

It hid because every textual search for it found something else — the
unrelated `--resume` flag on `bench/noise.py` (ledger-row resumption, which
creates NEW tasks and continues none), `runlock.py`'s docstring, the module's
own. The dead-code audit credited all of them until its reference rule was
made receiver-aware.

BOTH DIRECTIONS ARE TESTED HERE, and that is the point. A check that halts on
a broken chain but also halts on a task that merely never checkpointed is not
a stricter check, it is the denial of service D17 removed. So: break a chain
and require a halt; leave an anchor behind and require the task to run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.config import BackendConfig, Config  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import run_task  # noqa: E402
from agent.kernel.integrity import (  # noqa: E402
    ANCHOR_BEHIND,
    IntegrityError,
    checkpoint,
    classify_anchor,
)
from agent.kernel.tasks import create_task, load_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract, TaskStatus  # noqa: E402


def make_ctx(tmp_path):
    (tmp_path / "work").mkdir(exist_ok=True)
    cfg = Config(
        profile="mixed",
        db_path=str(tmp_path / "t.db"),
        anchor_path=str(tmp_path / "chain.anchor"),
        skills_path=str(tmp_path / "skills"),
        backends={"cloud": BackendConfig(kind="mock", model="m"),
                  "review": BackendConfig(kind="mock", model="r")},
        autonomy={"auto_review": False, "plan_required": False,
                  "approve_scoped_writes": True},
        root=str(tmp_path),
    )
    return AgentContext(cfg)


def new_task(ctx):
    return create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="do a thing", success_criterion="always_pass",
                     artifact_scope=["work"]),
        Budget(usd=1.0, seconds=120))


def run_once(ctx, task):
    ctx.backends["cloud"].queue('<final>{"answer": "first"}</final>')
    return run_task(ctx, task.id)


# ==========================================================================
# D20-4 — injected, both directions
# ==========================================================================

def test_a_broken_chain_halts_the_resume(tmp_path):
    """Injected, not reasoned about. A chain break destroys the ability to
    VERIFY, so anything concluded from the run is unfounded."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    run_once(ctx, t)

    ctx.conn.execute(
        "UPDATE events SET payload_json='{\"tampered\":1}' "
        "WHERE task_id=? AND seq=1", (t.id,))
    ctx.conn.commit()

    ctx.backends["cloud"].queue('<final>{"answer": "second"}</final>')
    with pytest.raises(IntegrityError, match="Chain broken"):
        run_task(ctx, t.id)

    assert load_task(ctx.conn, t.id).status is TaskStatus.FAILED, (
        "a halted task must be recorded as failed, not left running")
    assert ctx.conn.execute(
        "SELECT 1 FROM events WHERE task_id=? AND type='integrity.halt'",
        (t.id,)).fetchall(), "the halt itself must be on the record"
    ctx.close()


def test_an_anchor_merely_behind_resumes_normally(tmp_path):
    """The other direction, and the one a stricter check gets wrong. A task
    that ran past its last checkpoint is not tampered with — refusing it
    would reintroduce the denial of service D17 removed, in the one place
    that would make every un-checkpointed task unrunnable."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    run_once(ctx, t)

    checkpoint(ctx.log, ctx.anchor, t.id)
    for i in range(3):
        ctx.log.append(t.id, "task.budget_spent", {"i": i})
    assert classify_anchor(ctx.conn, ctx.log, ctx.anchor, t.id) == ANCHOR_BEHIND

    ctx.backends["cloud"].queue('<final>{"answer": "second"}</final>')
    result = run_task(ctx, t.id)
    assert result.status in ("done", "done_unannounced"), (
        f"a merely-behind anchor must not block a resume: {result.status}")
    ctx.close()


def test_a_rewritten_history_halts_even_though_the_chain_verifies(tmp_path):
    """The third case: every link hashes correctly, but the hash we anchored
    is gone. That is a rewrite, and it is what the anchor exists to catch."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    run_once(ctx, t)
    checkpoint(ctx.log, ctx.anchor, t.id)

    anchored = ctx.anchor.latest_chain(t.id)
    seq = ctx.conn.execute(
        "SELECT seq FROM events WHERE task_id=? AND hash=?",
        (t.id, anchored)).fetchone()[0]
    ctx.conn.execute("DELETE FROM events WHERE task_id=? AND seq >= ?",
                     (t.id, seq))
    ctx.conn.commit()
    ctx.log.append(t.id, "task.budget_spent", {"replacement": True})

    assert ctx.log.verify(t.id)[0] is True, "the chain itself still verifies"
    ctx.backends["cloud"].queue('<final>{"answer": "second"}</final>')
    with pytest.raises(IntegrityError, match="rewritten"):
        run_task(ctx, t.id)
    ctx.close()


# ==========================================================================
# D20-2 — one door, and everything comes through it
# ==========================================================================

def test_a_fresh_task_passes_the_door_trivially(tmp_path):
    """No fresh-versus-resumed branch. An empty chain verifies and has no
    anchor, so the check runs on every task — and a check that only runs on
    the resumed path is one nobody exercises until the day it matters."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    assert run_once(ctx, t).status in ("done", "done_unannounced")
    ctx.close()


def test_verification_happens_before_the_task_is_marked_running(tmp_path):
    """The chain is checked as it STANDS, not after this run has appended to
    it. Verifying after `set_status` would verify our own new event."""
    src = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(
        encoding="utf-8")
    i = src.index("def run_task")
    body = src[i:i + 3000]
    assert body.index("resume(ctx.conn") < body.index("set_status(ctx.conn"), (
        "resume() must run before the task is marked RUNNING")


def test_every_continuation_path_goes_through_run_task():
    """D20-1, pinned. Three call sites take a task id from OUTSIDE and can
    therefore continue an interrupted task: `alpha run`, and the control
    plane's foreground and background runs. The other six create the task
    immediately before running it.

    Phase 8 (a channel reviving a parked task) and Phase 11 (a scheduled
    wake) will each add another. They inherit the door by calling run_task,
    which is why the check lives there and not at the call sites.
    """
    import ast

    sites = []
    for f in list((ROOT / "src").rglob("*.py")) + list((ROOT / "bench").glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "run_task"):
                sites.append(f"{f.name}:{node.lineno}")
    assert sites, "run_task should have call sites"

    loop = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(
        encoding="utf-8")
    assert "resume(ctx.conn, task.id, ctx.log, ctx.anchor)" in loop, (
        "the door must be inside run_task, so every caller inherits it")


# ==========================================================================
# The keyspace collision the door exposed
# ==========================================================================

def test_a_plan_pin_does_not_read_as_a_rewritten_chain(tmp_path):
    """Plan approval used to write `plan:<hash>` under the TASK ID, the same
    key `checkpoint()` writes a chain head to. Whichever came last won.

    That broke both readers in opposite directions: the resume check saw a
    `plan:` value, could not find it among the task's event hashes, and
    called it a rewrite; and `verify_plan_pin` saw a chain head, found it did
    not start with `plan:`, and skipped its own check — which, since
    `run_task` checkpoints every run, means that check had effectively never
    fired.
    """
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    run_once(ctx, t)
    checkpoint(ctx.log, ctx.anchor, t.id)

    # A legacy-format entry, exactly as older anchor files hold it.
    ctx.anchor.record(t.id, "plan:deadbeef")

    assert ctx.anchor.latest(t.id) == "plan:deadbeef", "raw read sees it"
    assert ctx.anchor.latest_chain(t.id) is not None, (
        "the chain reader must skip it and find the real head")
    assert classify_anchor(ctx.conn, ctx.log, ctx.anchor, t.id) is None, (
        "a plan pin must not read as a rewritten history")

    ctx.backends["cloud"].queue('<final>{"answer": "second"}</final>')
    assert run_task(ctx, t.id).status in ("done", "done_unannounced")
    ctx.close()


def test_the_plan_pin_check_survives_a_later_checkpoint(tmp_path):
    """The other half. The pin has its own key now, so a chain checkpoint
    written afterwards cannot hide it."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    ctx.anchor.record(f"plan:{t.id}", "abc123")
    checkpoint(ctx.log, ctx.anchor, t.id)
    ctx.log.append(t.id, "task.budget_spent", {"i": 0})
    checkpoint(ctx.log, ctx.anchor, t.id)

    assert ctx.anchor.latest(f"plan:{t.id}") == "abc123", (
        "later checkpoints must not overwrite the plan pin")
    ctx.close()


def test_the_two_kinds_of_anchor_record_use_different_keys():
    planner = (ROOT / "src" / "agent" / "plan" / "planner.py").read_text(
        encoding="utf-8")
    assert 'record(f"plan:{task.id}"' in planner, (
        "the plan pin must have its own key, not share the task id with "
        "chain checkpoints")
    integrity = (ROOT / "src" / "agent" / "kernel" / "integrity.py").read_text(
        encoding="utf-8")
    assert "anchor.record(task_id, head)" in integrity
