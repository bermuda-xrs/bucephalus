"""scripts/mutate.py produces the mutants it claims to (iteration 3, 2.3).

A mutation tool whose operators silently do nothing reports every module at
100% - the instrument bug this project keeps finding in its own checks.
"""

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
    assert any("x != 1" in s for s in out)                  # comparison flip
    assert any("x == 1 or not y" in s for s in out)         # and -> or
    assert any("x == 1 and y:" in s for s in out)           # not dropped
    assert any("pass" in s and "raise" not in s for s in out)
    assert any("return x - 1" in s for s in out)            # + -> -
    assert any("return None" in s for s in out)
    assert any("ALLOW = ('goal',)" in s for s in out)       # allowlist member dropped
    assert any("x == 2" in s for s in out)                  # int + 1


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
