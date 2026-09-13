"""A3 - cron. Time is INJECTED, never waited for: every wake takes `now`.

The acceptance, one test each, plus the rules underneath them: the schedule is
a fact and its next fire is derived (11.5), an occurrence is claimed before it
acts, a missed window is logged and never replayed, and an unattended task
never auto-proceeds. Each test was run against the defect it names.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent import schedule as sched
from agent.context import AgentContext
from agent.execute.loop import run_task
from agent.instrument.report import run_report
from agent.kernel.tasks import create_task, load_task
from agent.kernel.types import Budget, TaskContract
from agent.schedule import Cron, CronError
from agent.security.exposure import UNRESTRICTED, ToolNotExposed
from agent.security.exposure import check as exposure_check
from agent.security.origin import SCHEDULE_DENIED

FINAL = '<final>{"answer": "done"}</final>'
WRITE = ('<action tool="write_file">'
         + json.dumps({"path": "work/report.txt", "content": "nightly"})
         + "</action>")


def _base() -> float:
    """A whole LOCAL hour, three hours ago. Cron fields are local time."""
    d = datetime.now().replace(minute=0, second=0, microsecond=0)
    return (d - timedelta(hours=3)).timestamp()


def _sched(ctx, spec="* * * * *", created=None):
    return sched.add(ctx.conn, spec,
                     TaskContract(goal="summarise the work folder",
                                  artifact_scope=["work"]),
                     Budget(usd=1.0, seconds=600), now=created)


def _scheduled_tasks(ctx) -> int:
    return ctx.conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE origin='schedule'").fetchone()[0]


def _actions(out):
    return [r["action"] for r in out]


# -- cron ----------------------------------------------------------------------

def test_cron_fields_and_the_next_occurrence():
    base = _base()
    assert Cron.parse("*/15 * * * *").next_after(base) == base + 15 * 60
    three = datetime.fromtimestamp(Cron.parse("0 3 * * *").next_after(base))
    assert (three.hour, three.minute) == (3, 0)
    weekday = datetime.fromtimestamp(Cron.parse("0 0 * * 1-5").next_after(base))
    assert weekday.isoweekday() <= 5
    leap = datetime.fromtimestamp(Cron.parse("0 0 29 2 *").next_after(
        datetime(2025, 3, 1).timestamp()))
    assert (leap.year, leap.month, leap.day) == (2028, 2, 29)


def test_both_day_fields_restricted_means_either():
    """The Vixie rule: "0 0 1 * 1" is the 1st of the month OR any Monday."""
    c = Cron.parse("0 0 1 * 1")
    days = [datetime.fromtimestamp(t) for t in _walk(c, datetime(2026, 9, 1), 6)]
    assert all(d.day == 1 or d.isoweekday() == 1 for d in days)
    assert any(d.day != 1 for d in days) and any(d.isoweekday() != 1 for d in days)


def _walk(c, start, n):
    t, out = start.timestamp() - 1, []
    for _ in range(n):
        t = c.next_after(t)
        out.append(t)
    return out


@pytest.mark.parametrize("bad", ["61 * * * *", "* * *", "a * * * *",
                                 "*/0 * * * *", "0 0 32 * *", "0 24 * * *"])
def test_a_bad_spec_is_refused_at_the_door(ctx, bad):
    with pytest.raises(CronError):
        _sched(ctx, spec=bad)


def test_last_at_or_before_inverts_next_after():
    c, base = Cron.parse("7,37 */2 * * *"), _base()
    for k in range(20):
        t = base + k * 997
        o = c.next_after(t)
        assert c.last_at_or_before(o, t) == o


# -- acceptance 1: fires and completes ------------------------------------------

def test_a_scheduled_task_fires_and_completes(ctx):
    base = _base()
    sid = _sched(ctx, created=base)
    ctx.backends["cloud"].queue(FINAL)

    out = sched.tick(ctx, now=base + 65)
    assert _actions(out) == ["fired"] and out[0]["occurrence"] == base + 60
    assert load_task(ctx.conn, out[0]["task_id"]).origin == "schedule"
    assert [r["status"] for r in sched.run_due(ctx)] == ["done"]
    assert sched.next_fire(ctx.conn, sched.get(ctx.conn, sid)) == base + 120


# -- acceptance 2: a USER boundary parks, notifies, and is actionable ----------

def test_a_boundary_parks_notifies_and_the_approval_is_actionable(ctx):
    ctx.config.autonomy.approve_scoped_writes = True  # would let a local write through
    base = _base()
    _sched(ctx, created=base)
    ctx.backends["cloud"].queue(WRITE)
    sched.tick(ctx, now=base + 65)
    [r] = sched.run_due(ctx)
    assert r["status"] == "paused", "unattended: it parks, whatever the flag says"

    [n] = sched.notifications(ctx.conn)
    assert (n["task_id"], n["kind"], n["detail"]["tool"]) == \
        (r["task_id"], "parked", "write_file")
    [a] = ctx.pending_approvals(r["task_id"])
    assert n["detail"]["approval_id"] == a["id"]

    ctx.resolve_approval(a["id"], True)
    ctx.backends["cloud"].queue(FINAL)
    [again] = sched.run_due(ctx)
    assert again["status"] == "done", "the next wake resumes it"
    out = Path(ctx.config.root) / "work" / "report.txt"
    assert out.read_text(encoding="utf-8") == "nightly", (
        "and the approved action happens - which until f11be4d it did not")


def test_the_deputy_never_sees_a_scheduled_task(ctx):
    ctx.config.autonomy.auto_review = True
    ctx.backends["review"].queue(json.dumps({"verdict": "approve",
                                             "rationale": "looks fine"}))
    base = _base()
    _sched(ctx, created=base)
    ctx.backends["cloud"].queue(WRITE)
    sched.tick(ctx, now=base + 65)
    [r] = sched.run_due(ctx)
    assert r["status"] == "paused"
    types = [t for (t,) in ctx.conn.execute(
        "SELECT type FROM events WHERE task_id=?", (r["task_id"],))]
    assert "review.skipped" in types and "review.requested" not in types


# -- acceptance 3: a missed schedule produces no backlog -------------------------

def test_a_dark_window_is_logged_once_and_not_replayed(ctx):
    base = _base()
    _sched(ctx, created=base)
    ctx.backends["cloud"].queue(FINAL, FINAL)
    sched.tick(ctx, now=base + 65)
    sched.run_due(ctx)

    out = sched.tick(ctx, now=base + 60 * 11 + 5)      # ten minutes dark
    fired = [r for r in out if r["action"] == "fired"]
    missed = [r for r in out if r["action"] == "missed"]
    assert [f["occurrence"] for f in fired] == [base + 660]
    assert len(missed) == 1 and missed[0]["count"] == 9, (
        "base+120 .. base+600 - nine occurrences, logged as ONE gap")
    assert _scheduled_tasks(ctx) == 2, "one task per firing, none per miss"


def test_an_occurrence_older_than_the_grace_is_missed_not_fired(ctx):
    base = _base()
    _sched(ctx, spec="0 * * * *", created=base - 1)
    assert _actions(sched.tick(ctx, now=base + 600)) == ["missed"]
    assert _scheduled_tasks(ctx) == 0
    assert _actions(sched.tick(ctx, now=base + 3600 + 30)) == ["fired"], (
        "and the next hour fires on time")


def test_a_parked_run_is_not_joined_by_a_copy_every_minute(ctx):
    base = _base()
    _sched(ctx, created=base)
    ctx.backends["cloud"].queue(WRITE)
    sched.tick(ctx, now=base + 65)
    [r] = sched.run_due(ctx)
    assert r["status"] == "paused"
    out = sched.tick(ctx, now=base + 125)
    assert _actions(out) == ["missed"] and out[0]["reason"] == "previous run unfinished"
    assert _scheduled_tasks(ctx) == 1


# -- acceptance 4: a restart neither double-fires nor drops ------------------

def test_a_restart_neither_double_fires_nor_drops_the_next(ctx):
    base = _base()
    _sched(ctx, created=base)
    assert _actions(sched.tick(ctx, now=base + 65)) == ["fired"]
    # The supervisor dies HERE - after claiming and firing, before running.

    second = AgentContext(ctx.config)          # and restarts
    try:
        second.backends["cloud"].queue(FINAL, FINAL)
        assert [r["status"] for r in sched.run_due(second)] == ["done"], (
            "the run claimed before the crash is not lost: run_due derives it")
        # Not merely refused by the primary key - not even ATTEMPTED. A wake
        # that re-derived from the start would report "already_claimed" here.
        assert sched.tick(second, now=base + 70) == [], "no double fire"
        assert _actions(sched.tick(second, now=base + 125)) == ["fired"], (
            "and the next occurrence is not dropped")
    finally:
        second.close()
    assert _scheduled_tasks(ctx) == 2


def test_an_occurrence_can_be_claimed_only_once(ctx):
    """Two supervisors computing the same due occurrence at the same instant:
    the primary key, not the derivation, is what refuses the second."""
    base = _base()
    sid = _sched(ctx, created=base)
    other = AgentContext(ctx.config)
    try:
        assert sched.claim(ctx.conn, sid, base + 60, "fired") is True
        assert sched.claim(other.conn, sid, base + 60, "fired") is False
    finally:
        other.close()


def test_the_next_fire_is_stored_nowhere(ctx):
    """11.5 as a schema fact: no column holds a computed next-fire time."""
    for table in ("schedules", "schedule_firings"):
        cols = [r[1] for r in ctx.conn.execute(f"PRAGMA table_info({table})")]
        assert not [c for c in cols if "next" in c], (table, cols)


# -- acceptance 5: telemetry separates scheduled runs ---------------------------

def test_scheduled_runs_are_separable_in_telemetry(ctx, task):
    base = _base()
    _sched(ctx, created=base)
    ctx.backends["cloud"].queue(FINAL, FINAL)
    sched.tick(ctx, now=base + 65)
    [r] = sched.run_due(ctx)
    run_task(ctx, task.id)                           # an interactive run

    s = run_report(ctx.conn, ctx.telemetry, r["task_id"])["provenance"]
    i = run_report(ctx.conn, ctx.telemetry, task.id)["provenance"]
    assert s["origin"] == "schedule" and s["schedule"]["occurrence"] == int(base + 60)
    assert i["origin"] == "local" and "schedule" not in i


# -- acceptance 6: the scheduled origin derives a tool floor --------------------

def test_the_scheduled_origin_derives_a_tool_floor(ctx):
    base = _base()
    _sched(ctx, created=base)
    [f] = [r for r in sched.tick(ctx, now=base + 65) if r["action"] == "fired"]

    second = AgentContext(ctx.config)
    try:
        policy = second.tool_policy(f["task_id"])
        assert SCHEDULE_DENIED <= policy.denied, (
            "derived from the row, so it survives the supervisor restarting")
        for tool in sorted(SCHEDULE_DENIED - {"delegate"}):
            with pytest.raises(ToolNotExposed):
                exposure_check(policy, tool)
    finally:
        second.close()

    local = create_task(ctx.conn, ctx.log,
                        TaskContract(goal="mine", artifact_scope=["work"]),
                        Budget(usd=1.0, seconds=60))
    assert ctx.tool_policy(local.id) is UNRESTRICTED
