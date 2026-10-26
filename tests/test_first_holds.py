from __future__ import annotations

import json

from agent.config import BackendConfig
from agent.execute.loop import EDIT_FIRST_NOTE, MAX_FIRST_HOLDS, run_task
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract

READ = '{"kind": "action", "tool": "read_file", "args": {"path": "work/a.txt"}}'
WRITE = '{"kind": "action", "tool": "write_file", "args": {"path": "work/a.txt", "content": "changed"}}'
ANSWER = '{"kind": "final", "answer": {"path": "work/a.txt"}}'
TOTAL = '{"kind": "final", "answer": {"total": 7}}'
CALC = '{"kind": "action", "tool": "write_file", "args": {"path": "work/calc.py", "content": "print(3 + 4)"}}'
RUN = '{"kind": "action", "tool": "run_command", "args": {"command": "python work/calc.py"}}'


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


def _task(ctx, **contract):
    return create_task(ctx.conn, ctx.log, TaskContract(artifact_scope=["work"], **contract), Budget(usd=1.0, seconds=600))


def test_edit_first_is_on_for_the_small_profile_and_compute_first_is_off_unless_declared():
    small = BackendConfig(kind="mock", model="m", tool_profile="small")
    assert small.on("edit_first") and not small.on("compute_first")
    assert not BackendConfig(kind="mock", model="m").on("edit_first")
    assert BackendConfig(kind="mock", model="m", compute_first=True).on("compute_first")


def test_an_answer_to_a_program_checked_task_waits_for_a_changed_file(small, workspace):
    (workspace / "a.txt").write_text("hi", encoding="utf-8")
    small.backends["cloud"].cfg.edit_first = True
    t = _task(small, goal="change a.txt", success_criterion="verify_program")
    small.backends["cloud"].queue(READ, ANSWER, WRITE, ANSWER)
    run_task(small, t.id)
    calls = small.backends["cloud"].calls
    assert len(_events(small, t, "edit_first.held")) == 1 and "no file has changed" in calls[2]["tail"]
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "changed"


def test_the_edit_hold_gives_up_after_its_cap(small, workspace):
    (workspace / "a.txt").write_text("hi", encoding="utf-8")
    small.backends["cloud"].cfg.edit_first = True
    t = _task(small, goal="change a.txt", success_criterion="verify_program")
    small.backends["cloud"].queue(READ, *[ANSWER] * (MAX_FIRST_HOLDS + 1))
    run_task(small, t.id)
    assert len(_events(small, t, "edit_first.held")) == MAX_FIRST_HOLDS
    assert EDIT_FIRST_NOTE


def test_a_numeric_answer_waits_until_a_script_ran(small, workspace):
    (workspace / "a.txt").write_text("3,4", encoding="utf-8")
    small.backends["cloud"].cfg.compute_first = True
    t = _task(small, goal="add the numbers", output_schema={"type": "object", "properties": {"total": {"type": "integer"}}})
    small.backends["cloud"].queue(READ, TOTAL, CALC, RUN, TOTAL)
    assert run_task(small, t.id).status.startswith("done")
    assert len(_events(small, t, "compute_first.held")) == 1
    assert "run_command" in small.backends["cloud"].calls[2]["tail"]


def test_neither_holds_a_task_it_does_not_fit(small, workspace):
    (workspace / "a.txt").write_text("hi", encoding="utf-8")
    small.backends["cloud"].cfg.edit_first = small.backends["cloud"].cfg.compute_first = True
    t = _task(small, goal="what does a.txt say", output_schema={"type": "object", "properties": {"text": {"type": "string"}}})
    small.backends["cloud"].queue(READ, '{"kind": "final", "answer": {"text": "hi"}}')
    assert run_task(small, t.id).status.startswith("done")
    assert not _events(small, t, "edit_first.held") and not _events(small, t, "compute_first.held")


def test_on_the_schema_route_the_final_branch_waits_for_the_edit(small, workspace):
    (workspace / "a.txt").write_text("hi", encoding="utf-8")
    cfg = small.backends["cloud"].cfg
    cfg.trust_tier, cfg.model_server, cfg.edit_first = "local", "ollama", True
    t = _task(small, goal="change a.txt", success_criterion="verify_program")
    small.backends["cloud"].queue(READ, WRITE, ANSWER)
    run_task(small, t.id)
    kinds = [[b["properties"]["kind"]["const"] for b in c["schema"]["anyOf"]] for c in small.backends["cloud"].calls]
    assert "final" not in kinds[1] and any("final" in k for k in kinds[2:]), [sorted(set(k)) for k in kinds]
    assert len(_events(small, t, "edit_first.held")) == 1  # later holds are verify-before-stop's, not this one's
