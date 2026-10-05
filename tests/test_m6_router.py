from __future__ import annotations

from agent.router.cascade import normalize


def test_router_respects_allowlist(ctx, task):
    assert ctx.router.choose("plan", task).name == "cloud"
    assert ctx.router.choose("review", task).name == "cloud"
    assert ctx.router.choose("step", task).name == "cloud"
    assert ctx.router.choose("summarize", task).name == "local"


def test_laptop_tier_is_narrower(ctx, task):
    ctx.config.active_local_tier = "laptop"
    assert ctx.router.choose("classify", task).name == "laptop"
    assert ctx.router.choose("summarize", task).name == "cloud"


def test_no_local_tier_falls_back_to_cloud(ctx, task):
    ctx.config.active_local_tier = None
    assert ctx.router.choose("summarize", task).name == "cloud"


def test_latency_aware_routing(ctx, task):
    task.budget.spent_seconds = task.budget.seconds - 10
    local = ctx.backends["local"]
    local.cfg.assumed_tokens_per_sec = 0.5
    assert ctx.router.choose("summarize", task).name == "cloud"
    local.cfg.assumed_tokens_per_sec = 10_000.0
    assert ctx.router.choose("summarize", task).name == "local"


def test_normalize_compares_parsed_objects():
    assert normalize('{"b":1,"a":2}') == normalize('{ "a": 2, "b": 1 }')
    assert normalize("  Yes  ") == normalize("yes")


def test_large_observations_route_to_the_local_tier(ctx, workspace, scripted):
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


def _same_model_local_tier(ctx):
    from agent.models.registry import build_backend

    ctx.config.backends["local"] = ctx.config.backends["cloud"].model_copy(
        update={"trust_tier": "local"})
    ctx.backends["local"] = build_backend("local", ctx.config.backends["local"])
    ctx.config.active_local_tier = "local"
    ctx.config.local_allowlist = ["compress_tool_output"]


def test_the_semantic_cache_is_reachable_and_hits(ctx, task, workspace, scripted):
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
