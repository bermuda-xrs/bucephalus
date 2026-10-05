from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.execute.libraries import (  # noqa: E402
    REGISTRY, LibraryRefused, LibraryRegistry, SandboxLibrary)
from agent.execute.sandbox import run_sandboxed  # noqa: E402


@pytest.mark.parametrize("field", ["uses_network", "writes_outside_scope",
                                   "spawns_subprocesses"])
def test_a_library_declaring_a_forbidden_capability_is_refused(field):
    r = LibraryRegistry()
    lib = SandboxLibrary(name="widener", version="1.0", purpose="x",
                         **{field: True})
    with pytest.raises(LibraryRefused, match="sandbox does not grant"):
        r.register(lib)
    assert r.names() == [], "a refused library must not land in the registry"


def test_an_unpinned_library_is_refused():
    r = LibraryRegistry()
    with pytest.raises(LibraryRefused, match="no pinned version"):
        r.register(SandboxLibrary(name="loose", version="", purpose="x"))


def test_the_shipped_registry_declares_nothing_forbidden():
    for lib in REGISTRY.all():
        assert not lib.uses_network
        assert not lib.writes_outside_scope
        assert not lib.spawns_subprocesses
        assert lib.version, f"{lib.name} is unpinned"


def test_the_manual_is_deterministic():
    assert REGISTRY.manual() == REGISTRY.manual()
    assert REGISTRY.names() == sorted(REGISTRY.names())


def _run(code: str, tmp_path):
    scope = tmp_path / "work"
    scope.mkdir(exist_ok=True)
    return run_sandboxed(code, allowed_paths=[str(scope)], timeout_s=120)


def test_sympy_solve_inside_the_sandbox(tmp_path):
    out = _run(
        "import sympy\n"
        "x = sympy.Symbol('x')\n"
        "print(sympy.solve(x**2 - 2, x))\n", tmp_path)
    assert out["returncode"] == 0, out
    assert "sqrt(2)" in out["stdout"], out["stdout"]


def test_sympy_simplify_inside_the_sandbox(tmp_path):
    out = _run(
        "import sympy\n"
        "x = sympy.Symbol('x')\n"
        "print(sympy.simplify((x**2 - 1)/(x - 1)))\n", tmp_path)
    assert out["returncode"] == 0, out
    assert "x + 1" in out["stdout"], out["stdout"]


def test_sympy_units_conversion_inside_the_sandbox(tmp_path):
    out = _run(
        "from sympy.physics.units import convert_to, kilometer, hour, meter, second\n"
        "v = 900 * kilometer / hour\n"
        "print(convert_to(v, meter/second))\n", tmp_path)
    assert out["returncode"] == 0, out
    assert "250" in out["stdout"], out["stdout"]


def test_the_sandbox_still_refuses_out_of_scope_writes_with_sympy_present(tmp_path):
    outside = tmp_path / "outside.txt"
    out = _run(
        "import sympy\n"
        f"open(r'{outside}', 'w').write('escaped')\n", tmp_path)
    assert not outside.exists(), (
        "sympy's presence must not widen the audit hook's scope confinement")
    assert out["returncode"] != 0 or "denied" in (out.get("stderr") or "").lower()
