"""D46 - parse_plan degrades on wrong-shaped JSON (plan/planner.py)."""

from __future__ import annotations

import pytest

from agent.plan.planner import parse_plan

DEGRADES = {
    "steps-an-int": '{"steps": 5}',
    "step-a-string": '{"steps": ["do the thing"]}',
    "tokens-words": '{"steps": [{"est_tokens": "about 500"}]}',
    "bad-taint": '{"steps": [{"max_taint_out": "radioactive"}]}',
    "a-list": '[1, 2, 3]',
    "tokens-nan": '{"steps": [{"est_tokens": NaN}]}',
    "tools-an-int": '{"steps": [{"tools": 3}]}',
}


@pytest.mark.parametrize("text", list(DEGRADES.values()), ids=list(DEGRADES))
def test_clean_wrong_shapes_degrade(text):
    p = parse_plan(text, "obj")
    assert p.steps == [] and p.unknowns


def test_FINDING_an_overflowing_number_still_raises():
    """json.loads turns 1e400 into float('inf'); int(inf) raises OverflowError,
    an ArithmeticError, which the D46 handler (ValueError, TypeError,
    AttributeError) does not catch - the 500 D46 closed, by one more shape."""
    with pytest.raises(OverflowError):
        parse_plan('{"steps": [{"est_tokens": 1e400}]}', "obj")


def test_note_deep_nesting():
    text = '{"steps": ' + "[" * 5000 + "]" * 5000 + "}"
    try:
        p = parse_plan(text, "obj")
        print("\ndeep nesting ->", p.unknowns)
    except RecursionError:
        print("\ndeep nesting -> RecursionError")


def test_FINDING_the_plan_route_500s_on_it(ctx, workspace, scripted):
    from fastapi.testclient import TestClient

    from agent.control.server import create_app
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=60))
    for b in ctx.backends:
        scripted(b, *['{"objective": "x", "steps": [{"intent": "i", "est_tokens": 1e400}]}'] * 3)
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    c.headers["Authorization"] = "Bearer " + app.state.token
    r = c.post(f"/tasks/{t.id}/plan")
    print("\nPOST /plan ->", r.status_code, r.text[:80])
    assert r.status_code == 500
