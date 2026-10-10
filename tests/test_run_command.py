from __future__ import annotations

import json

import pytest

from agent.execute.command import CommandRefused, to_program
from agent.execute.tools import tool_run_command


@pytest.mark.parametrize("command,verification", [
    ("pytest work/test_x.py -q", True),
    ("python -m pytest work", True),
    ("python -m unittest discover", True),
    ("python work/prog.py 1 2", True),
    ("ruff check work", True),
    ("python -m json.tool work/x.json", False),
])
def test_the_allowed_forms_and_whether_they_count_as_verification(command, verification):
    assert to_program(command)[1] is verification


@pytest.mark.parametrize("command", [
    "python -c 'import os; os.system(\"rm -rf /\")'", "rm -rf work", "git push", "curl http://x",
    "bash -c 'ls'", "python", "", "python -m", "powershell -Command x",
])
def test_everything_else_is_refused(command):
    with pytest.raises(CommandRefused):
        to_program(command)


def test_a_failing_test_run_reports_the_failure(ctx, task, workspace):
    (workspace / "test_x.py").write_text("def test_it():\n    assert 1 == 2\n", encoding="utf-8")
    r = tool_run_command(ctx, task, "pytest work/test_x.py -q -p no:cacheprovider")
    assert r["ok"] is False and r["verification"] is True and "1 failed" in r["stdout"], r


def test_a_program_runs_with_its_arguments(ctx, task, workspace):
    (workspace / "prog.py").write_text("import sys\nprint('sum', int(sys.argv[1]) + int(sys.argv[2]))\n", encoding="utf-8")
    r = tool_run_command(ctx, task, "python work/prog.py 2 3")
    assert r["ok"] is True and "sum 5" in r["stdout"], r


def test_a_refused_command_says_what_is_allowed(ctx, task):
    r = tool_run_command(ctx, task, "rm -rf work")
    assert r["ok"] is False and "python FILE" in r["error"]


def test_the_sandbox_still_confines_what_a_command_runs(ctx, task, workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    (workspace / "evil.py").write_text(f"open({str(outside)!r}, 'w').write('x')\n", encoding="utf-8")
    r = tool_run_command(ctx, task, "python work/evil.py")
    assert not outside.exists() and r["ok"] is False, json.dumps(r)[:300]
