from __future__ import annotations

import json

import pytest

from agent.execute.diagnostics import diagnose
from agent.execute.loop import run_task
from agent.kernel.tasks import create_task


def _act(tool, **args):
    return json.dumps({"kind": "action", "tool": tool, "args": args})


FINAL = '{"kind": "final", "answer": "ok"}'


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


@pytest.fixture
def sm(small):
    b = small.backends["cloud"].cfg
    b.trust_tier, b.model_server = "local", "ollama"
    b.reasoning_field = b.carry_field = False
    small.config.limits.max_steps = 14
    small.config.mechanisms.verify_before_stop = False
    return small


def _task(ctx, task):
    return create_task(ctx.conn, ctx.log, task.contract, task.budget)


@pytest.mark.parametrize("source, needle", [
    ("import re\nPAT = re.compile(r'[^\t-\\s]')\n", "TAB"),
    ("X = 'a\x00b'\n", "NUL"),
    ("import re\nPAT = re.compile('[^\\w\\s]')\n", "invalid escape"),
])
def test_d3_control_characters_and_broken_escapes_in_written_code_are_flagged(tmp_path, source, needle):
    p = tmp_path / "m.py"
    p.write_text(source, encoding="utf-8")
    out = diagnose(p)
    assert "escapes:" in out and needle in out and ("line 2" in out or "line 1" in out)


def test_d3_clean_code_says_so(tmp_path):
    p = tmp_path / "m.py"
    p.write_text("import re\nPAT = re.compile(r'[^\\w\\s-]')\n", encoding="utf-8")
    assert "escapes: ok" in diagnose(p)


def test_d2_alternating_between_two_calls_is_named(sm, task, workspace):
    sm.backends["cloud"].cfg.stuck_patterns = True
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    (workspace / "b.txt").write_text("b", encoding="utf-8")
    t = _task(sm, task)
    a, b = _act("read_file", path="work/a.txt"), _act("read_file", path="work/b.txt")
    sm.backends["cloud"].queue(a, b, a, b, FINAL)
    run_task(sm, t.id)
    assert [e["kind"] for e in _events(sm, t, "stuck.warned")] == ["alternation"]
    assert "alternating" in sm.backends["cloud"].calls[4]["tail"]


def test_d2_the_same_error_three_times_is_named(sm, task, workspace):
    sm.backends["cloud"].cfg.stuck_patterns = True
    t = _task(sm, task)
    sm.backends["cloud"].queue(*[_act("read_file", path=f"work/missing{i}.txt") for i in range(3)], FINAL)
    run_task(sm, t.id)
    assert "same_error" in [e["kind"] for e in _events(sm, t, "stuck.warned")]


def test_d2_steps_without_a_tool_call_are_named(sm, task, workspace):
    sm.backends["cloud"].cfg.stuck_patterns = True
    t = _task(sm, task)
    sm.backends["cloud"].queue(*["not json at all"] * 4, FINAL)
    run_task(sm, t.id)
    assert "no_tool" in [e["kind"] for e in _events(sm, t, "stuck.warned")]


def test_d2_is_off_when_declared_off(sm, task, workspace):
    sm.backends["cloud"].cfg.stuck_patterns = False
    t = _task(sm, task)
    sm.backends["cloud"].queue(*["not json at all"] * 4, FINAL)
    run_task(sm, t.id)
    assert not [e for e in _events(sm, t, "stuck.warned") if e.get("kind") == "no_tool"]


def test_d1_a_fresh_reviewer_can_return_the_answer_once(sm, task, workspace):
    sm.backends["cloud"].cfg.evaluator_pass = True
    (workspace / "a.txt").write_text("hello", encoding="utf-8")
    t = _task(sm, task)
    returned = json.dumps({"verdict": "return", "reason": "the answer ignores a.txt"})
    sm.backends["cloud"].queue(_act("read_file", path="work/a.txt"), FINAL, returned, FINAL)
    assert run_task(sm, t.id).status.startswith("done")
    calls = sm.backends["cloud"].calls
    review = calls[2]
    assert "<objective>" in review["tail"] and "<answer>" in review["tail"] and "read_file" not in review["tail"]
    assert review["schema"]["properties"]["verdict"]["enum"] == ["accept", "return"]
    assert "the answer ignores a.txt" in calls[3]["tail"] and len(calls) == 4
    assert len(_events(sm, t, "evaluator.returned")) == 1


def test_e1_hashline_reads_anchor_lines_and_edits_check_them(sm, task, workspace):
    from agent.execute.tools import line_anchor, tool_edit_lines, tool_read_file

    sm.backends["cloud"].cfg.edit_format = "hashline"
    (workspace / "c.ini").write_text("[a]\nx = 1\ny = 2\n", encoding="utf-8")
    shown = tool_read_file(sm, task, "work/c.ini")["content"]
    a2 = line_anchor(2, "x = 1")
    assert f"{a2}|x = 1" in shown
    ok = tool_edit_lines(sm, task, path="work/c.ini", start=a2, end=a2, content="x = 5")
    assert ok["ok"] and (workspace / "c.ini").read_text(encoding="utf-8") == "[a]\nx = 5\ny = 2\n"
    stale = tool_edit_lines(sm, task, path="work/c.ini", start=a2, end=a2, content="x = 9")
    assert not stale["ok"] and "read the file again" in stale["error"]


def test_e1_the_profile_gets_the_tool_only_in_hashline_format(small):
    from agent.context import AgentContext
    from conftest import make_config

    assert "edit_lines" in small.tools.names()
    for name, profile, fmt, has in (("full", "full", None, False), ("declared", "small", "default", False),
                                    ("forced", "full", "hashline", True)):
        cfg = make_config(__import__("pathlib").Path(small.config.root) / name)
        cfg.backends["cloud"].tool_profile = profile
        cfg.backends["cloud"].edit_format = fmt
        c = AgentContext(cfg)
        try:
            assert ("edit_lines" in c.tools.names()) is has, (profile, fmt)
        finally:
            c.close()


def test_d3_a_run_shows_the_escape_check_unless_declared_off(sm, task, workspace):
    sm.backends["cloud"].cfg.escape_check = False
    t = _task(sm, task)
    bad = "import re" + chr(10) + "PAT = re.compile(r'[^" + chr(9) + "-x]')" + chr(10)
    sm.backends["cloud"].queue(_act("write_file", path="work/m.py", content=bad), FINAL)
    run_task(sm, t.id)
    assert "escapes:" not in sm.backends["cloud"].calls[1]["tail"]
    sm.backends["cloud"].cfg.escape_check = True
    t2 = _task(sm, task)
    sm.backends["cloud"].queue(_act("write_file", path="work/m2.py", content=bad), FINAL)
    run_task(sm, t2.id)
    assert "TAB character" in sm.backends["cloud"].calls[-1]["tail"]


def test_review_the_evaluator_never_reads_through_a_link_or_outside_the_scope(sm, task, workspace, tmp_path):
    import shutil

    from conftest import make_dir_link

    sm.backends["cloud"].cfg.evaluator_pass = True
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("SECRET-OUTSIDE", encoding="utf-8")
    t = _task(sm, task)
    script = iter([_act("write_file", path="work/d/x.txt", content="mine"), "SWAP",
                   json.dumps({"verdict": "accept", "reason": "fine"})])

    def responder(prefix, tail):
        step = next(script, FINAL)
        if step == "SWAP":
            shutil.rmtree(workspace / "d")
            if not make_dir_link(workspace / "d", outside):
                pytest.skip("cannot make a link here")
            return FINAL
        return step

    sm.backends["cloud"]._responder = responder
    run_task(sm, t.id)
    review = next(c for c in sm.backends["cloud"].calls if "<objective>" in c["tail"])
    assert "SECRET-OUTSIDE" not in review["tail"]


def test_review_a_repeated_error_is_named_by_tool_without_quoting_it(sm, task, workspace):
    sm.backends["cloud"].cfg.stuck_patterns = True
    t = _task(sm, task)
    sm.backends["cloud"].queue(*[_act("read_file", path=f"work/missing{i}.txt") for i in range(3)], FINAL)
    run_task(sm, t.id)
    note = [c["tail"] for c in sm.backends["cloud"].calls if "failed the same way" in c["tail"]][0]
    stuck = note.split("failed the same way")[0].rsplit("<observation", 1)[1] + note.split("failed the same way")[1][:200]
    assert "read_file" in stuck and "no such file" not in stuck
