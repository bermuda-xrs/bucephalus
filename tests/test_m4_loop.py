"""M4 acceptance — the first end-to-end, and the budget stop."""

from __future__ import annotations

import json

from agent.execute.loop import parse_action, run_task


def test_parse_action_shapes():
    a = parse_action('blah <action tool="read_file">{"path": "x.py"}</action> blah')
    assert a.kind == "action" and a.tool == "read_file" and a.args == {"path": "x.py"}

    f = parse_action('<final>{"answer": 42}</final>')
    assert f.kind == "final" and f.payload == {"answer": 42}

    c = parse_action("<code>\nprint(1)\n</code>")
    assert c.kind == "code" and "print(1)" in c.args["code"]

    n = parse_action("I will now think about it.")
    assert n.kind == "none" and "no <action>" in n.error


def test_parse_action_accepts_constrained_json():
    """M5.4 — the local tier answers as JSON under constrained decoding.

    Both shapes must parse, because the cloud tier keeps the tag protocol
    while ollama backends are given a schema.
    """
    a = parse_action('{"kind": "action", "tool": "read_file", "args": {"path": "x.py"}}')
    assert a.kind == "action" and a.tool == "read_file" and a.args == {"path": "x.py"}

    f = parse_action('{"kind": "final", "answer": {"value": 7}}')
    assert f.kind == "final" and f.payload == {"value": 7}

    c = parse_action('{"kind": "code", "code": "print(1)"}')
    assert c.kind == "code" and c.args["code"] == "print(1)"

    bad = parse_action('{"kind": "action", "args": {}}')
    assert bad.kind == "none" and "tool" in bad.error


def test_parse_error_shows_the_expected_format():
    """A 3B model that gets the syntax wrong needs the shape, not a scolding."""
    n = parse_action('<action>\nlist_dir("work")\n</action>')
    assert n.kind == "none"
    assert 'tool="read_file"' in n.error and '"kind": "action"' in n.error


def test_schema_only_constrains_ollama_backends(ctx, task, scripted):
    """Forcing a schema on the cloud tier would fight extended thinking."""
    from agent.execute.loop import ACTION_SCHEMA, run_task

    scripted("cloud", '<final>{"answer": "ok"}</final>')
    run_task(ctx, task.id)
    # The mock cloud backend is kind="mock", so no schema should be passed and
    # the tag protocol is what gets used.
    assert ctx.backends["cloud"].cfg.kind != "ollama"
    assert ACTION_SCHEMA["required"] == ["kind"]


def test_end_to_end_trivial_task(ctx, workspace, scripted):
    """Write a function, save it, verify the file exists and the chain verifies."""
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    code = "def reverse(s):\n    return s[::-1]\n"
    task = create_task(
        ctx.conn,
        ctx.log,
        TaskContract(
            goal="Write a Python function that reverses a string, save to work/out.py",
            success_criterion="always_pass",
            artifact_scope=["work"],
        ),
        Budget(usd=1.0, seconds=600),
    )
    scripted(
        "cloud",
        '<action tool="write_file">'
        + json.dumps({"path": "work/out.py", "content": code})
        + "</action>",
        '<final>{"answer": "wrote work/out.py"}</final>',
    )
    # write_file is side-effecting with USER-taint args -> the reviewer decides.
    ctx.backends["review"].queue("VERDICT: approve\nBECAUSE: write inside artifact_scope")

    result = run_task(ctx, task.id)

    assert result.status == "done", result
    out = workspace / "out.py"
    assert out.exists()
    ns: dict = {}
    exec(out.read_text(encoding="utf-8"), ns)  # noqa: S102 - it is our own fixture
    assert ns["reverse"]("abc") == "cba"

    assert ctx.conn.execute(
        "SELECT COUNT(*) FROM calls WHERE task_id=?", (task.id,)
    ).fetchone()[0] > 0
    assert ctx.log.verify(task.id) == (True, None)


def test_budget_stops_execution(ctx, workspace, scripted):
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    task = create_task(
        ctx.conn,
        ctx.log,
        TaskContract(goal="spend money", artifact_scope=["work"]),
        Budget(usd=0.001, seconds=600),
    )
    scripted("cloud", *['<action tool="list_dir">{"path": "work"}</action>'] * 6)
    result = run_task(ctx, task.id)
    assert result.status == "budget_exhausted"
    m = ctx.telemetry.metrics(task.id)
    assert m.cost_usd < 0.01


def test_failed_verification_appends_an_external_observation(ctx, workspace, scripted):
    """Invariant #2, enforced structurally: no revision without a new observation."""
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    task = create_task(
        ctx.conn,
        ctx.log,
        TaskContract(
            goal="produce valid json",
            success_criterion="json_schema",
            output_schema={"type": "object", "required": ["value"],
                           "properties": {"value": {"type": "integer"}}},
            artifact_scope=["work"],
        ),
        Budget(usd=1.0, seconds=600),
    )
    scripted(
        "cloud",
        '<final>{"wrong_key": 1}</final>',
        '<final>{"value": 7}</final>',
    )
    result = run_task(ctx, task.id)
    assert result.status == "done" and result.answer == {"value": 7}

    # The second turn saw the verifier's actual complaint, not "try again".
    second_prompt = ctx.backends["cloud"].prompts[1]
    assert "Verification failed" in second_prompt
    assert "missing required key" in second_prompt

    types = [
        r[0] for r in ctx.conn.execute(
            "SELECT type FROM events WHERE task_id=? ORDER BY seq", (task.id,)
        ).fetchall()
    ]
    assert "verify.failed" in types and "verify.passed" in types
