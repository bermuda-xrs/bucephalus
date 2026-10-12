from __future__ import annotations

import json

import pytest

from agent.execute.loop import run_task


def _write(path, content):
    return '<action tool="write_file">' + json.dumps({"path": path, "content": content}) + "</action>"


FINAL = '<final>{"answer": "ok"}</final>'


@pytest.fixture
def writes(ctx, task):
    ctx.config.autonomy.approve_scoped_writes = True

    def go(*scripted_actions):
        ctx.backends["cloud"].queue(*scripted_actions, FINAL)
        run_task(ctx, task.id)
        return ctx.backends["cloud"].calls[len(scripted_actions)]["tail"]

    return go


def test_a_syntax_error_is_reported_with_its_line(writes):
    tail = writes(_write("work/a.py", "def f(:\n    pass\n"))
    assert "diagnostics" in tail and "SyntaxError" in tail and "line 1" in tail


def test_a_lint_finding_is_reported(writes):
    tail = writes(_write("work/a.py", "import os\n\n\ndef f():\n    return 1\n"))
    assert "F401" in tail and "py_compile: ok" in tail


def test_a_clean_file_says_so(writes):
    tail = writes(_write("work/a.py", "def f():\n    return 1\n"))
    assert "py_compile: ok" in tail and "ruff: clean" in tail


def test_other_files_get_no_diagnostics(writes):
    tail = writes(_write("work/notes.txt", "def f(:\n"))
    assert "diagnostics" not in tail


def test_an_edit_is_checked_too(writes, workspace):
    (workspace / "b.py").write_text("x = 1\n", encoding="utf-8")
    edit = '<action tool="edit_file">' + json.dumps({"path": "work/b.py", "old": "x = 1", "new": "x = (1"}) + "</action>"
    assert "SyntaxError" in writes(edit)


def test_the_mechanism_can_be_ablated(ctx, task, writes):
    ctx.config.mechanisms.diagnostics = False
    assert "diagnostics" not in writes(_write("work/a.py", "def f(:\n"))
    assert "diagnostics" in ctx.config.mechanisms.ablated()


def test_a_missing_ruff_is_said_not_hidden(writes, monkeypatch):
    from agent.execute import diagnostics

    monkeypatch.setattr(diagnostics, "ruff_command", lambda: None)
    tail = writes(_write("work/a.py", "x = 1\n"))
    assert "ruff: not available" in tail and "py_compile: ok" in tail


def test_a_broken_json_file_is_reported_with_its_position(writes):
    tail = writes(_write("work/a.json", '{"a": 1,}'))
    assert "diagnostics" in tail and "json:" in tail and "line 1" in tail


def test_a_good_json_file_says_so(writes):
    assert "json: ok" in writes(_write("work/a.json", '{"a": 1}'))


def test_a_broken_yaml_file_is_reported_with_its_line(writes):
    tail = writes(_write("work/a.yaml", "a: [1, 2\nb: 3\n"))
    assert "yaml:" in tail and "line" in tail and "ok" not in tail.split("yaml:")[1].split(chr(10))[0]


def test_a_good_yml_file_says_so(writes):
    assert "yaml: ok" in writes(_write("work/a.yml", "a: 1\nb:\n  - x\n"))


def test_a_broken_toml_file_is_reported(writes):
    tail = writes(_write("work/a.toml", "a = \n"))
    assert "toml:" in tail and "ok" not in tail.split("toml:")[1].split(chr(10))[0]


def test_a_good_toml_file_says_so(writes):
    assert "toml: ok" in writes(_write("work/a.toml", 'a = 1\n[t]\nb = "x"\n'))


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node is not installed")
def test_broken_javascript_is_reported_when_node_exists(writes):
    assert "SyntaxError" in writes(_write("work/a.js", "function (\n"))


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node is not installed")
def test_good_javascript_says_so_when_node_exists(writes):
    assert "node: ok" in writes(_write("work/b.js", "const x = 1;\n"))


def test_without_node_javascript_says_it_was_not_checked(tmp_path, monkeypatch):
    from agent.execute import diagnostics

    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
    p = tmp_path / "c.js"
    p.write_text("const x = 1;" + chr(10), encoding="utf-8")
    assert "node: not available" in diagnostics.diagnose(p)


def test_other_text_files_are_still_left_alone(writes):
    assert "diagnostics" not in writes(_write("work/a.md", "# {broken"))
