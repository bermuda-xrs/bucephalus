from __future__ import annotations

from agent.config import BackendConfig
from agent.execute.loop import REPEAT_NOTE, run_task
from agent.execute.tools import tool_write_file
from test_first_holds import READ, WRITE, _task

BAD = '{"kind": "final", "answer": {"total": "seven"}}'
SCHEMA = {"type": "object", "properties": {"total": {"type": "integer"}}, "required": ["total"]}


def test_both_are_off_unless_declared():
    small = BackendConfig(kind="mock", model="m", tool_profile="small")
    assert not small.on("write_diff") and not small.on("repeat_note")


def test_overwriting_a_file_shows_the_lines_it_removed_and_added(small, workspace):
    (workspace / "g.ini").write_text("[a]\nx = 1\ny = 2\nz = 3\n", encoding="utf-8")
    small.backends["cloud"].cfg.write_diff = True
    out = tool_write_file(small, None, "work/g.ini", "[a]\nx = 9\ny = 2\n[log]\nf = 1\n")
    assert out["removed"] == ["x = 1", "z = 3"] and out["added"] == ["x = 9", "[log]", "f = 1"]
    assert "removed" not in tool_write_file(small, None, "work/new.ini", "[a]\n")
    small.backends["cloud"].cfg.write_diff = False
    assert "removed" not in tool_write_file(small, None, "work/g.ini", "[a]\n")


def test_a_long_diff_is_cut_and_says_how_many_lines_it_left_out(small, workspace):
    (workspace / "g.ini").write_text("".join(f"k{i} = {i}\n" for i in range(40)), encoding="utf-8")
    small.backends["cloud"].cfg.write_diff = True
    out = tool_write_file(small, None, "work/g.ini", "")
    assert len(out["removed"]) == 13 and out["removed"][-1] == "... 28 more"


def test_resubmitting_a_failed_answer_unchanged_is_named(small, workspace):
    (workspace / "a.txt").write_text("7", encoding="utf-8")
    small.backends["cloud"].cfg.repeat_note = True
    t = _task(small, goal="what number", output_schema=SCHEMA, success_criterion="json_schema")
    small.backends["cloud"].queue(READ, BAD, BAD, BAD)
    run_task(small, t.id)
    tails = [c["tail"] for c in small.backends["cloud"].calls]
    assert REPEAT_NOTE not in tails[2] and REPEAT_NOTE in tails[3]


def test_no_note_after_a_file_changed_or_when_off(small, workspace):
    (workspace / "a.txt").write_text("7", encoding="utf-8")
    cfg = small.backends["cloud"].cfg
    cfg.repeat_note = True
    t = _task(small, goal="what number", output_schema=SCHEMA, success_criterion="json_schema")
    small.backends["cloud"].queue(READ, BAD, WRITE, BAD, BAD)
    run_task(small, t.id)
    tails = [c["tail"] for c in small.backends["cloud"].calls]
    after_write = [x for x in tails[3:] if "Verification failed" in x[-600:]]
    assert after_write and REPEAT_NOTE not in after_write[0]
    cfg.repeat_note = False
    t2 = _task(small, goal="what number", output_schema=SCHEMA, success_criterion="json_schema")
    small.backends["cloud"].queue(READ, BAD, BAD, BAD)
    run_task(small, t2.id)
    assert not any(REPEAT_NOTE in c["tail"] for c in small.backends["cloud"].calls[-4:])
