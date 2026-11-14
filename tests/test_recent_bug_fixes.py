from __future__ import annotations

from rich.console import Console

from agent.cli import cmd_alerts
from agent.control import conversations
from agent.control.analytics import usage
from agent.kernel.removal import remove_tasks
from test_console_delete import _fill


def _removed_conversation(ctx):
    c = conversations.create(ctx.conn, "gone")
    tid = conversations.add_message(ctx, c["id"], "private", ["work"], None, None)["task_id"]
    _fill(ctx, tid)
    return tid


def test_analytics_keeps_the_spend_of_a_removed_task(ctx, workspace):
    tid = _removed_conversation(ctx)
    before = usage(ctx.conn, 7)["totals"]
    remove_tasks(ctx, [tid])
    after = usage(ctx.conn, 7)["totals"]
    assert (after["input_tokens"], after["calls"]) == (before["input_tokens"], before["calls"]) and before["calls"] > 0


def test_analytics_still_leaves_out_measurement_runs(ctx, workspace):
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    t = create_task(ctx.conn, ctx.log, TaskContract(goal="arm", artifact_scope=["work"]), Budget(usd=1, seconds=9),
                    origin="bench").id
    ctx.conn.execute("INSERT INTO calls(task_id, backend, purpose, input_tokens, output_tokens, created_at) "
                     "VALUES (?,?,?,?,?,strftime('%s','now'))", (t, "mock", "step", 99, 1))
    ctx.conn.commit()
    assert usage(ctx.conn, 7)["totals"]["calls"] == 0


def test_the_alerts_command_raises_no_alarm_for_a_removal(ctx, workspace):
    tid = _removed_conversation(ctx)
    remove_tasks(ctx, [tid])
    con = Console(record=True, width=200)
    cmd_alerts(ctx, None, con)
    out = con.export_text()
    assert "MISMATCH" not in out and "removed on purpose" in out


def test_the_alerts_command_still_alarms_on_tampering(ctx, workspace):
    tid = _removed_conversation(ctx)
    ctx.conn.execute("UPDATE events SET payload_json='{}' WHERE task_id=? AND seq=1", (tid,))
    ctx.conn.commit()
    con = Console(record=True, width=200)
    cmd_alerts(ctx, None, con)
    assert "MISMATCH" in con.export_text()


def test_the_trace_graph_starts_fresh_when_the_task_changes():
    from test_console_reskin_guards import UI

    view = UI[UI.index("function forceView("):UI.index("function dropForceView(")]
    assert "S.fgTask !== t.task" in view and "S.fgTask = t.task" in view


def test_deleting_the_conversation_on_screen_also_clears_its_trace():
    from test_console_reskin_guards import UI

    fn = UI[UI.index("async function deleteConversation("):UI.index("async function archiveConversation(")]
    assert "S.traceTask" in fn and "renderTrace()" in fn


def test_a_removal_holds_the_chain_lock_so_no_append_lands_inside_it():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "agent" / "kernel" / "removal.py").read_text(encoding="utf-8")
    assert "with chain_lock(conn):" in src
    assert src.index("with chain_lock(conn):") < src.index("ctx.anchor.record(TOMBSTONE + t, last[t])")
