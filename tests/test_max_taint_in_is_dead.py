from __future__ import annotations

import json
from pathlib import Path

from agent import HARNESS_VERSION
from agent.kernel.types import TaskContract
from agent.ledger.prefix import PrefixBuilder

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"


def test_the_field_is_gone_from_the_contract():
    assert "max_taint_in" not in TaskContract.model_fields
    assert "max_taint_in" not in TaskContract(goal="x").model_dump()


def test_nothing_names_it_in_code():
    hits = [f"{p.relative_to(SRC).as_posix()}:{i}: {line.strip()}"
            for p in sorted(SRC.rglob("*.py"))
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if "max_taint_in" in line and not line.lstrip().startswith("#")]
    assert not hits, "max_taint_in survives in code:\n" + "\n".join(hits)


def test_its_bytes_are_gone_from_the_prefix():
    assert HARNESS_VERSION >= 3, (
        "the field's bytes leave only with the bump; HARNESS_VERSION went back")
    contract = json.loads(TaskContract(goal="x").model_dump_json())
    prefix = PrefixBuilder("SYSTEM", [], contract).build()
    assert "max_taint_in" not in prefix


def test_a_contract_stored_before_the_bump_still_loads():
    old = TaskContract.model_validate_json(
        '{"goal": "x", "max_taint_in": "untrusted", "artifact_scope": ["work"]}')
    assert old.goal == "x"
    assert old.artifact_scope == ["work"]
    assert not hasattr(old, "max_taint_in")
