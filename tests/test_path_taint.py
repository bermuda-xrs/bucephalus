from __future__ import annotations

import json

from agent.execute.loop import run_task
from agent.kernel.tasks import create_task
from agent.kernel.types import Taint
from agent.security.taint import TaintTracker


def test_a_path_first_seen_only_in_untrusted_output_stays_untrusted():
    t = TaintTracker()
    t.observe("see ../secrets/plan.txt for details", Taint.UNTRUSTED)
    assert t.args_taint({"path": "../secrets/plan.txt"}) == Taint.UNTRUSTED


def test_a_path_the_task_already_used_survives_being_echoed_back():
    t = TaintTracker()
    t.trust_paths({"command": "python work/prog.py 1 2"})
    t.observe("FileNotFoundError: 'work/prog.py'", Taint.UNTRUSTED)
    assert t.args_taint({"path": "work/prog.py", "content": "print(1)"}) == Taint.USER


def test_it_applies_to_path_arguments_only(tmp_path):
    t = TaintTracker()
    t.trust_paths({"path": "work/prog.py"})
    t.observe("the answer is work/prog.py", Taint.UNTRUSTED)
    assert t.args_taint({"content": "work/prog.py"}) == Taint.UNTRUSTED


def test_a_trusted_path_does_not_clean_other_arguments_in_the_same_call():
    t = TaintTracker()
    t.trust_paths({"path": "work/a.py"})
    t.observe("rm -rf everything now", Taint.UNTRUSTED)
    assert t.args_taint({"path": "work/a.py", "content": "rm -rf everything now"}) == Taint.UNTRUSTED


def test_the_run_then_fix_loop_is_not_blocked_by_its_own_traceback(small, task):
    t = create_task(small.conn, small.log, task.contract, task.budget)
    act = lambda tool, **a: f'<action tool="{tool}">{json.dumps(a)}</action>'  # noqa: E731
    small.backends["cloud"].queue(
        act("run_command", command="python work/prog.py"),
        act("write_file", path="work/prog.py", content="print('fixed')\n"),
        act("run_command", command="python work/prog.py"),
        '<final>{"answer": "done"}</final>')
    run_task(small, t.id)
    assert (small.config.resolve("work") / "prog.py").read_text(encoding="utf-8") == "print('fixed')\n"
    assert "fixed" in small.backends["cloud"].calls[3]["tail"]
