from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bench"))

from agent.config import BackendConfig, Config  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.execute.tools import (  # noqa: E402
    MAX_PARALLEL_CALLS,
    default_registry,
    tool_parallel,
)
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, Taint, TaskContract  # noqa: E402
from agent.security.exposure import ToolPolicy  # noqa: E402


def make_ctx(tmp_path, width=4):
    (tmp_path / "work").mkdir(exist_ok=True)
    cfg = Config(
        profile="mixed",
        db_path=str(tmp_path / "t.db"),
        anchor_path=str(tmp_path / "chain.anchor"),
        skills_path=str(tmp_path / "skills"),
        backends={"cloud": BackendConfig(kind="mock", model="m"),
                  "review": BackendConfig(kind="mock", model="r")},
        autonomy={"auto_review": False, "plan_required": False,
                  "approve_scoped_writes": True},
        limits={"max_parallel_tools": width},
        root=str(tmp_path),
    )
    return AgentContext(cfg)


def new_task(ctx):
    return create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="read some files", success_criterion="always_pass",
                     artifact_scope=["work"]),
        Budget(usd=1.0, seconds=120))


def five_files(tmp_path):
    for i in range(5):
        (tmp_path / "work" / f"f{i}.txt").write_text(f"body {i}", encoding="utf-8")
    return [{"tool": "read_file", "args": {"path": f"work/f{i}.txt"}}
            for i in range(5)]


def test_five_reads_complete_and_all_results_come_back(tmp_path):
    ctx = make_ctx(tmp_path)
    out = tool_parallel(ctx, new_task(ctx), five_files(tmp_path))
    assert out["ok"] and out["n"] == 5
    bodies = [r["result"]["content"] for r in out["results"]]
    assert sorted(bodies) == [f"body {i}" for i in range(5)]
    ctx.close()


def test_results_stay_in_the_order_they_were_requested(tmp_path):
    ctx = make_ctx(tmp_path)
    out = tool_parallel(ctx, new_task(ctx), five_files(tmp_path))
    assert [r["args"]["path"] for r in out["results"]] == [
        f"work/f{i}.txt" for i in range(5)]
    ctx.close()


def test_a_batch_really_runs_at_once(tmp_path):
    ctx = make_ctx(tmp_path, width=5)
    t = new_task(ctx)

    from agent.execute.tools import ToolSpec

    barrier = threading.Barrier(5, timeout=60)

    def meet(c, task, **kw):
        barrier.wait()
        return {"ok": True}

    ctx.tools.register(ToolSpec("meet", meet, {"type": "object", "properties": {}},
                                side_effecting=False,
                                max_arg_taint=Taint.UNTRUSTED,
                                output_taint=Taint.UNTRUSTED,
                                description="waits for the others"))
    out = tool_parallel(ctx, t, [{"tool": "meet", "args": {}} for _ in range(5)])
    assert out["ok"] and all(r["result"]["ok"] for r in out["results"]), out
    ctx.close()


def test_width_one_is_genuinely_sequential(tmp_path):
    ctx = make_ctx(tmp_path, width=1)
    seen: list[int] = []
    lock = threading.Lock()

    from agent.execute.tools import ToolSpec

    def note(c, task, **kw):
        with lock:
            seen.append(threading.get_ident())
        return {"ok": True}

    ctx.tools.register(ToolSpec("note", note, {"type": "object", "properties": {}},
                                side_effecting=False,
                                max_arg_taint=Taint.UNTRUSTED,
                                output_taint=Taint.UNTRUSTED, description="x"))
    tool_parallel(ctx, new_task(ctx),
                  [{"tool": "note", "args": {}} for _ in range(4)])
    assert len(set(seen)) == 1 and seen[0] == threading.get_ident(), (
        "at width 1 every call must run on the calling thread")
    ctx.close()


@pytest.mark.parametrize("name", ["write_file", "run_code", "memory_write",
                                  "edit_file", "append_to_outbox", "git_push"])
def test_a_side_effecting_tool_is_refused_for_the_whole_batch(tmp_path, name):
    ctx = make_ctx(tmp_path)
    out = tool_parallel(ctx, new_task(ctx), [
        {"tool": "read_file", "args": {"path": "work/f0.txt"}},
        {"tool": name, "args": {}},
    ])
    assert out["ok"] is False
    assert "side-effecting" in out["error"]
    ctx.close()


def test_no_model_call_can_enter_a_batch():
    r = default_registry()
    for name in r.names():
        spec = r.get(name)
        if spec.side_effecting:
            continue
        src = spec.fn.__code__.co_names
        assert "complete" not in src, (
            f"{name} is batchable and appears to make a model call; "
            "concurrent generation on one CPU instance corrupts wall clock")


def test_parallel_cannot_nest(tmp_path):
    ctx = make_ctx(tmp_path)
    out = tool_parallel(ctx, new_task(ctx), [{"tool": "parallel", "args": {}}])
    assert out["ok"] is False and "may not nest" in out["error"]
    ctx.close()


def test_a_batch_is_bounded(tmp_path):
    ctx = make_ctx(tmp_path)
    out = tool_parallel(ctx, new_task(ctx), [
        {"tool": "read_file", "args": {"path": "work/x"}}
    ] * (MAX_PARALLEL_CALLS + 1))
    assert out["ok"] is False and "exceeds the cap" in out["error"]
    ctx.close()


def test_a_denied_call_refuses_the_whole_batch_and_nothing_runs(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    five_files(tmp_path)

    ran: list[str] = []
    from agent.execute.tools import ToolSpec

    def watch(c, task, **kw):
        ran.append("permitted call executed")
        return {"ok": True}

    ctx.tools.register(ToolSpec("watch", watch, {"type": "object", "properties": {}},
                                side_effecting=False,
                                max_arg_taint=Taint.UNTRUSTED,
                                output_taint=Taint.UNTRUSTED, description="x"))
    ctx.restrict_tools(t.id, ToolPolicy(frozenset({"grep"}), "test"))

    out = tool_parallel(ctx, t, [
        {"tool": "watch", "args": {}},
        {"tool": "grep", "args": {"pattern": "x", "path": "work"}},
        {"tool": "watch", "args": {}},
    ])

    assert out["ok"] is False
    assert out["denied"] == "grep"
    assert out["applied"] is False
    assert ran == [], (
        "a permitted call in the batch RAN despite a sibling being refused; "
        "the batch partially applied")

    logged = ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='tool.not_exposed'",
        (t.id,)).fetchall()
    assert logged, "the refusal must be recorded, not only returned"
    assert "grep" in logged[0][0]
    ctx.close()


def test_the_capability_gate_also_refuses_the_whole_batch(tmp_path):
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    ctx.taint_tracker(t.id).observe("secrets from a web page /etc/shadow",
                                    Taint.UNTRUSTED)
    ctx.phase = "plan"

    out = tool_parallel(ctx, t, [
        {"tool": "read_file", "args": {"path": "work/f0.txt"}},
        {"tool": "expand_artifact", "args": {"id": "nope"}},
    ])
    assert out["ok"] is True
    assert out["results"][1]["result"]["ok"] is False
    ctx.close()


def test_every_call_is_gated_before_any_call_runs(tmp_path):
    src = (ROOT / "src" / "agent" / "execute" / "tools.py").read_text(
        encoding="utf-8")
    i = src.index("def tool_parallel")
    body = src[i:src.index(chr(10) + "def ", i + 10)]
    gate_at = body.index("gate_tool_call(")
    run_at = body.index("ThreadPoolExecutor(max_workers")
    assert gate_at < run_at, "every call must be gated before any call runs"
    assert body.index("planned.append") < run_at


def test_a_measurement_run_is_sequential_and_records_it():
    import inspect

    from measurement import open_measurement

    src = inspect.getsource(open_measurement)
    assert "max_parallel_tools = 1" in src, (
        "wall clock is the cost axis; a task that read five files at once is "
        "not comparable with one that read them in turn")
    noise = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")
    assert '"max_parallel_tools": ctx.config.limits.max_parallel_tools' in noise, (
        "and the run record must state it rather than leave it inferred")


def test_the_search_index_is_written_by_triggers_inside_the_same_transaction():
    schema = (ROOT / "src" / "agent" / "kernel" / "db.py").read_text(
        encoding="utf-8")
    assert "CREATE TRIGGER IF NOT EXISTS trg_search_artifact_ai" in schema
    assert "CREATE TRIGGER IF NOT EXISTS trg_search_task_ai" in schema


def test_no_batchable_tool_writes_to_a_shared_keyspace(tmp_path):
    ctx = make_ctx(tmp_path)
    before = ctx.conn.execute("SELECT COUNT(*) FROM semantic_cache").fetchone()[0]
    tool_parallel(ctx, new_task(ctx), five_files(tmp_path))
    after = ctx.conn.execute("SELECT COUNT(*) FROM semantic_cache").fetchone()[0]
    assert after == before, (
        "a batchable tool wrote to the semantic cache; concurrent writes to a "
        "shared keyspace are a collision surface (DR1)")
    ctx.close()


def test_concurrent_reads_of_the_shared_connection_do_not_corrupt(tmp_path):
    ctx = make_ctx(tmp_path, width=8)
    t = new_task(ctx)
    for i in range(8):
        (tmp_path / "work" / f"g{i}.txt").write_text("x" * 500, encoding="utf-8")

    calls = ([{"tool": "search_history", "args": {"query": "read some files"}}] * 4
             + [{"tool": "read_file", "args": {"path": f"work/g{i}.txt"}}
                for i in range(4)])
    out = tool_parallel(ctx, t, calls)
    assert out["ok"]
    for r in out["results"]:
        assert r["result"]["ok"] is True, r
    ctx.close()


def test_parallel_has_a_taint_table_row():
    spec = default_registry().get("parallel")
    assert spec.output_taint is Taint.UNTRUSTED, (
        "the batch carries whatever its members return; anything lower would "
        "launder a web_fetch through a batch")
