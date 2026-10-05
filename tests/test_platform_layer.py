from __future__ import annotations

import ast
import re
from pathlib import Path

from agent.platform import INTERFACE

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"
PLATFORM = SRC / "platform"
MODULES = [p for p in PLATFORM.glob("*.py") if p.name != "__init__.py"]


def _defined(path: Path) -> set[str]:
    out = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.FunctionDef):
            out.add(n.name)
        elif isinstance(n, ast.ImportFrom) and n.level == 1:
            out.update(a.asname or a.name for a in n.names)
    return out


def test_there_is_a_module_per_platform():
    assert {p.stem for p in MODULES} >= {"windows", "posix", "darwin"}


def test_every_platform_module_fills_in_the_whole_contract():
    for path in MODULES:
        missing = set(INTERFACE) - _defined(path)
        assert not missing, f"{path.name} lacks {sorted(missing)}"


_CONDITIONAL = re.compile(r"sys\.platform|os\.name\s*[!=]=|IS_POSIX|IS_WINDOWS")


def test_no_platform_conditional_outside_the_layer():
    found = []
    for f in SRC.rglob("*.py"):
        if PLATFORM in f.parents:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _CONDITIONAL.search(line) and not line.lstrip().startswith("#"):
                found.append(f"{f.relative_to(SRC)}:{i}: {line.strip()}")
    assert not found, "platform checks outside agent/platform:\n" + "\n".join(found)
