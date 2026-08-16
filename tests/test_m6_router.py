"""M6 acceptance — allowlist, escalation on disagreement, latency-aware routing.

Also covers the local-tier CALL SITE: the router being correct is worthless if
nothing in the loop ever asks it for a local-eligible purpose.
"""

from __future__ import annotations

from agent.router.cascade import local_with_escalation, max_cluster_fraction, normalize


def test_router_respects_allowlist(ctx, task):
    """A 'plan' purpose never routes local, whatever the measurements say."""
    assert ctx.router.choose("plan", task).name == "cloud"
    assert ctx.router.choose("review", task).name == "cloud"
    assert ctx.router.choose("step", task).name == "cloud"
    assert ctx.router.choose("summarize", task).name == "local"


def test_laptop_tier_is_narrower(ctx, task):
    ctx.config.active_local_tier = "laptop"
    assert ctx.router.choose("classify", task).name == "laptop"
    # summarize is on the desktop allowlist but not the laptop one
    assert ctx.router.choose("summarize", task).name == "cloud"


def test_no_local_tier_falls_back_to_cloud(ctx, task):
    ctx.config.active_local_tier = None
    assert ctx.router.choose("summarize", task).name == "cloud"


def test_latency_aware_routing(ctx, task):
    """Tight time budget -> cloud even though local is free."""
    ctx.backends["local"].set_throughput(0.5)  # half a token per second
    task.budget.spent_seconds = task.budget.seconds - 1  # ~1s left
    assert ctx.router.choose("summarize", task).name == "cloud"


def test_normalize_compares_parsed_objects():
    assert normalize('{"b":1,"a":2}') == normalize('{ "a": 2, "b": 1 }')
    assert normalize("  Yes  ") == normalize("yes")


def test_agreement_fraction():
    assert max_cluster_fraction(["a", "a", "b"]) == 2 / 3
    assert max_cluster_fraction(["a", "b", "c"]) == 1 / 3


def test_two_of_three_agreement_does_not_escalate(ctx, task):
    """The spec's literal 0.67 fails this case; 2/3 is 0.6667."""
    ctx.backends["local"].queue("same", "same", "different")
    resp, escalated = local_with_escalation(ctx, task, "summarize", "P", "T", k=3)
    assert escalated is False
    assert resp.backend == "local"


def test_escalation_on_disagreement(ctx, task):
    """Three divergent local samples -> cloud is called and escalated=1 is recorded."""
    ctx.backends["local"].queue("alpha", "beta", "gamma")
    ctx.backends["cloud"].queue("the cloud answer")

    resp, escalated = local_with_escalation(ctx, task, "summarize", "P", "T", k=3)
    assert escalated is True
    assert resp.backend == "cloud"
    rows = ctx.conn.execute(
        "SELECT COUNT(*) FROM calls WHERE task_id=? AND escalated=1", (task.id,)
    ).fetchone()[0]
    assert rows == 1


def test_escalation_rate_is_a_health_metric(ctx, task):
    ctx.backends["local"].queue("a", "b", "c")
    ctx.backends["cloud"].queue("x")
    local_with_escalation(ctx, task, "summarize", "P", "T", k=3)
    health = ctx.router.health()
    assert 0.0 <= health["escalation_rate"] <= 1.0
    assert health["verdict"] in {
        "healthy", "local tier is out of its depth",
        "too cautious — savings left unclaimed", "no data",
    }


# -- the call site ---------------------------------------------------------


def test_large_observations_route_to_the_local_tier(ctx, workspace, scripted):
    """The loop must actually ISSUE a local-eligible purpose, or the free
    column stays at zero forever no matter how good the router is."""
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    ctx.config.compress_observations_over_tokens = 50
    big = "alpha beta gamma delta epsilon zeta eta theta iota kappa " * 200
    (workspace / "big.txt").write_text(big, encoding="utf-8")

    task = create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="read the big file", artifact_scope=["work"]),
        Budget(usd=5.0, seconds=600),
    )
    scripted("cloud", '<action tool="read_file">{"path": "work/big.txt"}</action>',
             '<final>{"answer": "read it"}</final>')
    ctx.backends["local"].queue("The file lists greek letters, repeatedly.")

    run_task(ctx, task.id)

    purposes = [
        r[0] for r in ctx.conn.execute(
            "SELECT purpose FROM calls WHERE task_id=?", (task.id,)
        ).fetchall()
    ]
    assert "compress_tool_output" in purposes
    backend = ctx.conn.execute(
        "SELECT backend FROM calls WHERE task_id=? AND purpose='compress_tool_output'",
        (task.id,),
    ).fetchone()[0]
    assert backend == "local"

    m = ctx.telemetry.metrics(task.id)
    assert m.free_tokens > 0, "the local tier did no work"
    assert m.paid_tokens > 0


def test_compression_skipped_when_no_local_tier(ctx, workspace, scripted):
    """Paying cloud rates to shrink a cloud prompt is a net loss. Don't."""
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    ctx.config.active_local_tier = None
    ctx.config.compress_observations_over_tokens = 50
    (workspace / "big.txt").write_text("word " * 2000, encoding="utf-8")

    task = create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="read it", artifact_scope=["work"]),
        Budget(usd=5.0, seconds=600),
    )
    scripted("cloud", '<action tool="read_file">{"path": "work/big.txt"}</action>',
             '<final>{"answer": "ok"}</final>')
    run_task(ctx, task.id)

    purposes = {
        r[0] for r in ctx.conn.execute(
            "SELECT purpose FROM calls WHERE task_id=?", (task.id,)
        ).fetchall()
    }
    assert "compress_tool_output" not in purposes
