from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "mutate", Path(__file__).resolve().parents[1] / "scripts" / "mutate.py")
mutate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mutate)

SRC = '''
ALLOW = ("goal", "scope")


def f(x, y):
    """doc"""
    if x == 1 and not y:
        raise ValueError("no")
    return x + 1
'''


def _all() -> set[str]:
    return {mutate.apply(SRC, m) for m in mutate.mutants(SRC)}


def test_each_operator_changes_the_code_it_names():
    out = _all()
    assert any("x != 1" in s for s in out)
    assert any("x == 1 or not y" in s for s in out)
    assert any("x == 1 and y:" in s for s in out)
    assert any("pass" in s and "raise" not in s for s in out)
    assert any("return x - 1" in s for s in out)
    assert any("return None" in s for s in out)
    assert any("ALLOW = ('goal',)" in s for s in out)
    assert any("x == 2" in s for s in out)


def test_a_mutant_is_one_change_and_docstrings_are_left_alone():
    base = mutate.apply.__globals__["ast"].unparse(mutate.ast.parse(SRC))
    for m in mutate.mutants(SRC):
        s = mutate.apply(SRC, m)
        assert s != base, m
        assert "doc" in s
    assert not any(m["op"] == "bool" for m in mutate.mutants('"""doc"""\n'))


def test_only_restricts_the_scope():
    assert {m["line"] for m in mutate.mutants(SRC, {"ALLOW"})} == {2}


def test_only_can_name_one_method():
    src = ("class C:\n    def a(self):\n        return 1 == 1\n"
           "    def b(self):\n        return 2 == 2\n")
    assert {m["line"] for m in mutate.mutants(src, {"C.b"})} == {5}


import json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODULE = str(ROOT / "src" / "agent" / "security" / "origin.py")


def _recheck_with(outcome, tmp_path, monkeypatch, killer=None):
    calls = []

    def fake(tree_dir, tests, timeout, why=None):
        calls.append(tests)
        if tests[0] == "tests":
            if len([c for c in calls if c[0] == "tests"]) == 1:
                return "survived", 1.0
            if why is not None and killer:
                why.append(killer)
            return outcome, 1.0
        return "survived", 1.0
    monkeypatch.setattr(mutate, "_run", fake)
    out = tmp_path / "r.json"
    mutate.main([MODULE, "tests/test_origin.py", "--only", "is_channel", "--jobs", "1",
                 "--recheck", "--out", str(out)])
    return json.loads(out.read_text(encoding="utf-8"))


def test_a_timeout_in_the_recheck_is_undecided_not_a_kill(tmp_path, monkeypatch):
    res = _recheck_with("timeout", tmp_path, monkeypatch)
    assert res["results"] and all(r["status"] == "timeout" for r in res["results"])


def test_a_recheck_kill_names_the_test_that_made_it(tmp_path, monkeypatch):
    res = _recheck_with("killed", tmp_path, monkeypatch, killer="tests/test_x.py::test_y")
    assert all(r["status"] == "killed-by-suite" and r["killed_by"] == ["tests/test_x.py::test_y"]
               for r in res["results"])
