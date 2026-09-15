"""D38 - `max_taint_in` is dead in code and alive in bytes until the next bump.

No check could give it a meaning (SPEC-DELTA C60). It stays declared only
because the whole contract is rendered into every prompt prefix, and removing
it is a HARNESS_VERSION bump - batched with the next one that happens anyway.
These hold both halves: nothing reads it and nothing can set it, and its
bytes leave only with a bump.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import HARNESS_VERSION
from agent.kernel.types import TaskContract, Taint
from agent.ledger.prefix import PrefixBuilder

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"


def test_nothing_reads_it():
    hits = [f"{p.relative_to(SRC).as_posix()}:{i}: {line.strip()}"
            for p in sorted(SRC.rglob("*.py"))
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if "max_taint_in" in line and not line.lstrip().startswith("#")]
    outside = [h for h in hits if not h.startswith("kernel/types.py:")]
    assert not outside, "max_taint_in is read outside its declaration:\n" + "\n".join(outside)


def test_no_ceiling_can_be_declared():
    for ceiling in (Taint.TRUSTED, Taint.USER):
        with pytest.raises(ValueError, match="D38"):
            TaskContract(goal="x", max_taint_in=ceiling)
    assert TaskContract(goal="x").max_taint_in == Taint.UNTRUSTED
    # Every stored contract carries the default, and still loads.
    old = TaskContract.model_validate_json('{"goal": "x", "max_taint_in": "untrusted"}')
    assert old.max_taint_in == Taint.UNTRUSTED


def test_its_bytes_leave_only_with_a_bump():
    contract = json.loads(TaskContract(goal="x").model_dump_json())
    prefix = PrefixBuilder("SYSTEM", [], contract).build()
    if HARNESS_VERSION == 2:
        assert "max_taint_in" in prefix, (
            "removed from the prefix without the HARNESS_VERSION bump it waits for (D38)")
    else:
        assert "max_taint_in" not in prefix, (
            "HARNESS_VERSION moved past 2; the dead field's bytes go with that bump (D38)")
