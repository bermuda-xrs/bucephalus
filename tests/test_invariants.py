from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"

REPLAYABLE_MODULES = [
    SRC / "execute" / "loop.py",
    SRC / "ledger" / "compact.py",
    SRC / "ledger" / "prefix.py",
    SRC / "security" / "capabilities.py",
    SRC / "security" / "taint.py",
]

FORBIDDEN_CALLS = {
    "uuid4": "generate ids in the kernel and record them as events",
    "random": "seed explicitly or read the value from the log",
    "randint": "seed explicitly or read the value from the log",
    "shuffle": "sort instead; unordered iteration changes the prefix",
}


def _calls(tree: ast.AST) -> list[tuple[str, int]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name:
                out.append((name, node.lineno))
    return out


@pytest.mark.parametrize("path", REPLAYABLE_MODULES, ids=lambda p: p.name)
def test_replayable_code_is_deterministic(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = [
        f"{path.name}:{line} calls {name}() — {why}"
        for name, line in _calls(tree)
        for why in [FORBIDDEN_CALLS.get(name)]
        if why
    ]
    assert not bad, "; ".join(bad)


def test_prefix_builder_never_reads_the_clock():
    from agent.ledger import prefix

    src = inspect.getsource(prefix)
    for banned in ("time.time", "datetime.now", "utcnow"):
        assert banned not in src, f"{banned} in the prefix builder"


def test_monitor_is_not_imported_by_prompt_builders():
    prompt_builders = [
        SRC / "ledger" / "prefix.py",
        SRC / "execute" / "loop.py",
        SRC / "plan" / "planner.py",
        SRC / "plan" / "review.py",
    ]
    for path in prompt_builders:
        text = path.read_text(encoding="utf-8")
        assert "monitor_flags" not in text, f"{path.name} reads monitor_flags"
        assert "assure.monitor" not in text, f"{path.name} imports the monitor"


def test_events_table_is_append_only():
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for stmt in ("update events", "delete from events"):
            if stmt in text:
                offenders.append(f"{path.name}: {stmt}")
    assert not offenders, offenders


def test_side_effecting_tools_route_through_execute_once():
    from agent.execute.tools import default_registry

    reg = default_registry()
    side_effecting = [t for t in reg._tools.values() if t.side_effecting]  # noqa: SLF001
    assert side_effecting, "no side-effecting tools registered"

    loop_src = (SRC / "execute" / "loop.py").read_text(encoding="utf-8")
    code_src = (SRC / "execute" / "codeact.py").read_text(encoding="utf-8")
    assert "execute_once(" in loop_src
    assert "execute_once(" in code_src
