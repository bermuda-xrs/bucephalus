"""M6 acceptance — allowlist, escalation on disagreement, latency-aware routing.

Also covers the local-tier CALL SITE: the router being correct is worthless if
nothing in the loop ever asks it for a local-eligible purpose.
"""

from __future__ import annotations

from agent.router.cascade import local_with_escalation, max_cluster_fraction, normalize


def test_router_respects_allowlist(ctx, task, routing_only):
    """A 'plan' purpose never routes local, whatever the measurements say."""
    assert ctx.router.choose("plan", task).name == "cloud"
    assert ctx.router.choose("review", task).name == "cloud"
    assert ctx.router.choose("step", task).name == "cloud"
    assert ctx.router.choose("summarize", task).name == "local"


def test_laptop_tier_is_narrower(ctx, task, routing_only):
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


def test_large_observations_route_to_the_local_tier(ctx, workspace, scripted, routing_only):
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


# -- one backend wearing two names is not two tiers (2026-09-22) ------------
#
# The M6.3 guard raised whenever `active_local_tier` resolved to a backend and
# a purpose was allowlisted to it. That is right when a REAL second tier is
# configured and escalation has no call site. It was wrong for
# config.local.yaml, whose `local` slot is the same model on the same endpoint
# as the executor: there is nothing to escalate TO, and raising made the
# semantic cache unmeasurable - the compression path needs a local tier, and
# asking for one crashed the task before `cached_op` was ever reached.


def _same_model_local_tier(ctx):
    """config.local.yaml's shape: same kind, model and endpoint, declared local."""
    from agent.models.registry import build_backend

    ctx.config.backends["local"] = ctx.config.backends["cloud"].model_copy(
        update={"trust_tier": "local"})
    # A SEPARATE instance, not an alias to the cloud backend: aliasing makes
    # a scripted("local", ...) call append to the EXECUTOR's own queue, which
    # quietly rewrites the script the test just set up.
    ctx.backends["local"] = build_backend("local", ctx.config.backends["local"])
    ctx.config.active_local_tier = "local"
    ctx.config.local_allowlist = ["compress_tool_output"]


def test_a_local_tier_that_is_the_executor_does_not_raise(ctx, task):
    _same_model_local_tier(ctx)
    ctx.router.choose("compress_tool_output", task)      # must not raise
    note = ctx.config.profile_provenance.get("escalation", "")
    assert "not reachable" in note and "same backend" in note, (
        "a one-tier config that looks routed must still say so in the record")


def test_a_genuinely_different_second_tier_still_raises(ctx, task):
    """The guard is NARROWED, not removed. A different model behind the local
    tier is a real second tier with a dead escalation path, and that is the
    case the guard exists for."""
    import pytest

    ctx.config.active_local_tier = "local"       # conftest's 'local' is mock-local
    ctx.config.local_allowlist = ["compress_tool_output"]
    assert ctx.config.backends["local"].model != ctx.config.backends["cloud"].model
    with pytest.raises(RuntimeError, match="escalation-by-disagreement"):
        ctx.router.choose("compress_tool_output", task)


def test_the_semantic_cache_is_reachable_and_hits(ctx, task, workspace, scripted):
    """The whole point of the narrowing: `cached_op` runs, and the second
    identical compression is served from the store rather than re-summarised.
    Before it, this task ended `crashed` with 0 cache rows."""
    import json

    from agent.execute.loop import run_task

    _same_model_local_tier(ctx)
    ctx.config.compress_observations_over_tokens = 50
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    (workspace / "a.txt").write_text(
        "File a. " + " ".join(f"a-{k} value {k * 7}" for k in range(90)),
        encoding="utf-8")
    read = '<action tool="read_file">' + json.dumps({"path": "work/a.txt"}) + "</action>"
    scripted("cloud", read, read, '<final>{"answer": "done"}</final>')
    scripted("local", "the file lists a-N value pairs",
             "the file lists a-N value pairs")

    result = run_task(ctx, task.id)
    assert result.status != "crashed", result.status
    compressions = ctx.conn.execute(
        "SELECT COUNT(*) FROM calls WHERE purpose='compress_tool_output'"
    ).fetchone()[0]
    hits = ctx.conn.execute(
        "SELECT COALESCE(SUM(hits),0) FROM semantic_cache").fetchone()[0]
    assert compressions >= 1, "the compression path is still unreachable"
    assert hits >= 1, (
        f"{compressions} compressions and {hits} hits: two identical reads "
        "should summarise once and serve the second from the cache")
