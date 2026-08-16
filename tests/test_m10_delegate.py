"""M10 acceptance — five gates, and a parent context that stays bounded."""

from __future__ import annotations

import pytest

from agent.delegate.contracts import SubtaskPlan, may_delegate, run_delegated
from agent.kernel.types import Budget


def _full(ctx) -> SubtaskPlan:
    return SubtaskPlan(
        goal="read the pile and report only what matters",
        estimated_irrelevant_tokens=50_000,
        output_schema={"type": "object", "required": ["findings"],
                       "properties": {"findings": {"type": "string"}}},
        success_criterion="json_schema",
        budget=Budget(usd=0.10, seconds=60),
        is_tightly_coupled=False,
        artifact_scope=["work"],
    )


def test_delegation_rejects_incomplete_contract(ctx, task):
    """Each of the five gates independently blocks delegation."""
    assert may_delegate(ctx, task, _full(ctx))[0] is True

    cases = {
        "insufficient isolation benefit": {"estimated_irrelevant_tokens": 10},
        "no typed output schema": {"output_schema": None},
        "no checkable success criterion": {"success_criterion": "not_registered"},
        "no budget": {"budget": None},
        "tightly coupled — never delegate": {"is_tightly_coupled": True},
    }
    for expected, change in cases.items():
        sub = _full(ctx)
        for k, v in change.items():
            setattr(sub, k, v)
        ok, why = may_delegate(ctx, task, sub)
        assert ok is False and why == expected


def test_parent_context_bounded(ctx, task, workspace, scripted):
    """Child produces 50k tokens; the parent's context grows by < 500 tokens."""
    from agent.tokens import count_tokens

    huge = "irrelevant detail. " * 12_000
    scripted("cloud", f'<final>{{"findings": "{huge}"}}</final>')

    result = run_delegated(ctx, task, _full(ctx))

    assert count_tokens(huge) > 20_000
    assert count_tokens(result.render()) < 500
    assert not hasattr(result, "transcript")
    assert result.artifact_id and result.child_task_id


def test_delegation_is_logged_on_both_sides(ctx, task, workspace, scripted):
    scripted("cloud", '<final>{"findings": "small"}</final>')
    run_delegated(ctx, task, _full(ctx))
    types = [
        r[0] for r in ctx.conn.execute(
            "SELECT type FROM events WHERE task_id=? ORDER BY seq", (task.id,)
        ).fetchall()
    ]
    assert "delegation.opened" in types and "delegation.closed" in types


def test_refused_delegation_raises_rather_than_degrading(ctx, task):
    sub = _full(ctx)
    sub.budget = None
    with pytest.raises(ValueError, match="no budget"):
        run_delegated(ctx, task, sub)


def test_overflow_falls_back_to_compaction_when_gates_fail(ctx, task, workspace):
    """M3.4 says overflow delegates; M10 says most delegations are refused.
    The overflow path must still terminate, not thrash."""
    from agent.kernel.types import Taint
    from agent.ledger.entries import LedgerEntry

    entries = [
        LedgerEntry.note("goal", pinned=True),
        LedgerEntry.observation("x " * 5000, taint=Taint.UNTRUSTED),
    ]
    out = ctx.handle_overflow_by_delegation(task, entries)
    assert any(e.pinned for e in out)
    assert len(out) <= len(entries) + 1
