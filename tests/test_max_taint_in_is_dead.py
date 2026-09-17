"""D38 - `max_taint_in` is gone, in code AND in bytes, as of HARNESS_VERSION 3.

No check could give it a meaning (SPEC-DELTA C60), so it was made dead in code
on 2026-09-15 and left declared for one reason: the whole contract renders into
every prompt prefix, and removing it moves bytes the model reads. That is a
HARNESS_VERSION bump, and the entry set a deadline for taking one for this
alone. The deadline arrived 2026-09-17; the bump is 2 -> 3.

These hold the end state. The file keeps its name so the entry's history stays
findable from the test that closed it.
"""

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
    """Not "nothing READS it" any more - nothing mentions it outside a comment.
    A labelled legacy constant in its place was considered and rejected: the
    model would still have read a ceiling nothing enforces, which is the state
    this deletion exists to end."""
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
    """283 tasks in the measurement record carry the key in their stored JSON.
    The bump makes their NUMBERS incomparable, which is what a bump is for; it
    must not make their ROWS unreadable, which would be data loss dressed up as
    versioning. Pydantic ignores unknown fields, and this pins that."""
    old = TaskContract.model_validate_json(
        '{"goal": "x", "max_taint_in": "untrusted", "artifact_scope": ["work"]}')
    assert old.goal == "x"
    assert old.artifact_scope == ["work"]
    assert not hasattr(old, "max_taint_in")
