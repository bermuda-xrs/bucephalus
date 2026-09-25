"""The planner's read-only survey respects the protected-path fence
(AUDIT-2026-09 F5, D43).

`gate_tool_call` refuses `read_file` of the agent's own state - secrets,
databases, graders. The planner's survey read each artifact_scope entry
directly, and pasted the first 2000 bytes into the planning prompt: the one
file route the gate did not cover (DR3). Read-only is not unrestricted.
"""

from __future__ import annotations

from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.plan.planner import draft_plan

SECRET = "SENTINEL-DOTENV-7731"


def _draft(ctx, scope):
    t = create_task(ctx.conn, ctx.log, TaskContract(goal="plan it", artifact_scope=scope),
                    Budget(usd=1.0, seconds=60))
    draft_plan(ctx, t)
    return "\n".join(ctx.backends["cloud"].prompts)


def test_a_protected_file_in_scope_is_not_read_into_the_prompt(ctx, workspace):
    (workspace.parent / ".env").write_text(f"KEY={SECRET}\n", encoding="utf-8")
    prompt = _draft(ctx, [".env"])
    assert SECRET not in prompt, "the planner read a protected file into its prompt"
    assert ".env" in prompt and "protected" in prompt, "the refusal is not visible to the planner"


def test_a_scope_that_contains_protected_state_is_not_listed(ctx, workspace):
    """The gate refuses list_dir('.') because it walks into the database;
    the survey must not list it either."""
    prompt = _draft(ctx, ["."])
    assert "agent.db" not in prompt, prompt[-600:]


def test_an_ordinary_scope_is_still_surveyed(ctx, workspace):
    (workspace / "notes.txt").write_text("ordinary contents 123", encoding="utf-8")
    prompt = _draft(ctx, ["work/notes.txt"])
    assert "ordinary contents 123" in prompt
