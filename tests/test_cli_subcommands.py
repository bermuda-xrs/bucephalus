from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _mock(model: str, **kw) -> dict:
    return {"kind": "mock", "model": model, "trust_tier": "cloud",
            "context_window": 200000, "usable_context_window": 200000,
            "reports_prompt_cache": True, **kw}


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    import yaml

    from agent import schedule as sched
    from agent.config import (AutonomyConfig, BackendConfig, Config,
                              GateConfig, Limits)
    from agent.context import AgentContext
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, Taint, TaskContract
    from agent.memory.store import MemoryStore, record_fingerprint
    from agent.paths import ensure_state_dirs
    from agent.plan.planner import approve_plan, draft_plan

    root = tmp_path_factory.mktemp("bucephalus_cli")
    ensure_state_dirs(root)
    (root / "policy.md").write_text(
        "# policy\n\nDefault is DENY.\n\n"
        "## Approve when all of these hold\n"
        "- tool is write_file\n- paths inside artifact_scope\n",
        encoding="utf-8")
    (root / "a_skill.md").write_text(
        "# units\n\nState the unit beside every number.\n", encoding="utf-8")

    db = root / "agent.db"
    anchor = root / "anchor" / "chain.anchor"
    skills = root / "skills_store"

    backends = {
        "cloud": _mock("mock-cloud", input_cost_per_mtok=5.0,
                       output_cost_per_mtok=25.0, cache_read_per_mtok=0.5,
                       cache_write_per_mtok=6.25),
        "review": _mock("mock-review", input_cost_per_mtok=3.0,
                        output_cost_per_mtok=15.0),
        "local": _mock("mock-local", trust_tier="local", context_window=120000,
                       usable_context_window=120000),
        "laptop": _mock("mock-laptop", trust_tier="local", context_window=32768,
                        usable_context_window=32768),
    }
    raw = {
        "profile": "mixed",
        "db_path": str(db), "anchor_path": str(anchor), "skills_path": str(skills),
        "backends": backends,
        "active_local_tier": "local",
        "limits": {"max_steps": 6, "default_task_budget_usd": 1.0},
        "local_allowlist": ["summarize", "classify", "extract",
                            "compress_tool_output", "draft_candidate"],
        "gate": {"min_paired_trials": 30, "min_lift_pp": 5.0,
                 "max_p_value": 0.05, "min_discordant_pairs": 10},
        "autonomy": {"plan_required": False, "auto_review": True,
                     "auto_review_backend": "review",
                     "auto_review_policy": str(root / "policy.md")},
    }
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    cfg = Config(
        db_path=str(db), anchor_path=str(anchor), skills_path=str(skills),
        root=str(root),
        backends={k: BackendConfig(**v) for k, v in backends.items()},
        active_local_tier="local",
        limits=Limits(max_steps=6, default_task_budget_usd=1.0),
        gate=GateConfig(),
        autonomy=AutonomyConfig(plan_required=False, auto_review=True,
                                auto_review_backend="review",
                                auto_review_policy=str(root / "policy.md")),
    )
    ctx = AgentContext(cfg)
    seeded = create_task(
        ctx.conn, ctx.log,
        TaskContract(goal="seeded task for the CLI sweep", artifact_scope=["work"]),
        Budget(usd=1.0, seconds=600))
    plan = draft_plan(ctx, seeded)
    approve_plan(ctx, seeded, plan)
    run_task(ctx, seeded.id)

    approval_id = ctx.request_approval(
        seeded, ctx.tools.get("write_file"),
        {"path": "work/x.txt", "content": "hello"},
        "seeded: something for `bucephalus approvals` to decide", 0)

    skill_id = ctx.skills.store(
        name="units", body=(root / "a_skill.md").read_text(encoding="utf-8"),
        contract={"applies_when": "numbers appear"},
        provenance={"drafted_by": "human"}, status="candidate")

    schedule_id = sched.add(
        ctx.conn, "0 4 * * *",
        TaskContract(goal="seeded schedule", artifact_scope=["work"]),
        Budget(usd=1.0, seconds=600))

    store = MemoryStore(root / "memory")
    store.write("user", "prefers metric units", Taint.USER)
    record_fingerprint(store)
    ctx.close()

    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                        "OPENAI_API_KEY", "BUCEPHALUS_TOKEN")}
    env.update({"PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "BUCEPHALUS_DATA_DIR": str(root),
                "BUCEPHALUS_CONFIG_DIR": str(root),
                "COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"})

    class Runner:
        def __call__(self, *argv, timeout=180, expect=0):
            r = subprocess.run(  # noqa: S603
                [sys.executable, "-m", "agent.cli", "--config",
                 str(config_path), *argv],
                cwd=str(ROOT), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout)
            if expect is not None:
                assert r.returncode == expect, (
                    f"bucephalus {' '.join(argv)} -> exit {r.returncode}, "
                    f"expected {expect}\nSTDOUT:\n{r.stdout[-1500:]}\n"
                    f"STDERR:\n{r.stderr[-1500:]}")
            assert "Traceback (most recent call last)" not in r.stderr, \
                r.stderr[-1500:]
            return r

        def spawn(self, *argv):
            return subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "agent.cli", "--config",
                 str(config_path), *argv],
                cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace")

        def new_task(self, goal="a fresh task"):
            out = self("new", goal, "--scope", "work").stdout
            return out.split("task ", 1)[1].split()[0].strip()

        def conn(self):
            from agent.kernel.db import open_db

            return open_db(db)

    run = Runner()
    run.root = root
    run.config_path = config_path
    run.env = env
    run.seeded_task = seeded.id
    run.approval_id = approval_id
    run.skill_id = skill_id
    run.schedule_id = schedule_id
    return run


def test_the_fixture_seeded_something(cli):
    c = cli.conn()
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("tasks", "plans", "approvals", "skills", "schedules",
                        "calls", "sessions", "events")}
    c.close()
    assert all(n > 0 for n in counts.values()), counts


def test_new(cli):
    tid = cli.new_task("write a haiku about hash chains")
    c = cli.conn()
    row = c.execute("SELECT id FROM tasks WHERE id=?", (tid,)).fetchone()
    c.close()
    assert row is not None, f"`new` printed {tid} but wrote no row"


def test_new_without_scope_says_an_empty_scope_permits_nothing(cli):
    r = cli("new", "a task with no scope")
    assert "empty scope permits nothing" in r.stdout


def test_plan(cli):
    tid = cli.new_task("plan me")
    r = cli("plan", tid)
    c = cli.conn()
    row = c.execute("SELECT status FROM plans WHERE task_id=?", (tid,)).fetchone()
    c.close()
    assert row is not None and row[0] == "drafted", r.stdout[-600:]


def test_approve_pins_the_plan_to_the_anchor(cli):
    tid = cli.new_task("approve me")
    cli("plan", tid)
    r = cli("approve", tid)
    c = cli.conn()
    row = c.execute(
        "SELECT id, approved_hash FROM plans WHERE task_id=? AND status='approved'",
        (tid,)).fetchone()
    c.close()
    assert row is not None, r.stdout[-600:]
    assert row[1] == row[0], "approved without pinning its own hash"


def test_approve_refuses_a_stale_plan_hash(cli):
    tid = cli.new_task("approve the wrong hash")
    cli("plan", tid)
    r = cli("approve", tid, "0" * 64, expect=1)
    assert "mismatch" in r.stdout
    c = cli.conn()
    n = c.execute(
        "SELECT COUNT(*) FROM plans WHERE task_id=? AND status='approved'",
        (tid,)).fetchone()[0]
    c.close()
    assert n == 0, "a mismatched hash still approved something"


def test_reject(cli):
    tid = cli.new_task("reject me")
    cli("plan", tid)
    cli("reject", tid, "--comment", "the second step is not reversible")
    c = cli.conn()
    row = c.execute("SELECT status FROM plans WHERE task_id=?", (tid,)).fetchone()
    c.close()
    assert row[0] == "rejected"


def test_run(cli):
    tid = cli.new_task("run me")
    r = cli("run", tid)
    assert "paid" in r.stdout and "cache hit" in r.stdout, r.stdout[-400:]
    c = cli.conn()
    status = c.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    c.close()
    assert status == "done", status


def test_run_prints_n_a_when_the_backend_reports_no_cache(cli, tmp_path):
    import yaml

    raw = yaml.safe_load(cli.config_path.read_text(encoding="utf-8"))
    for b in raw["backends"].values():
        b["reports_prompt_cache"] = False
    silent = tmp_path / "config.nocache.yaml"
    silent.write_text(yaml.safe_dump(raw), encoding="utf-8")
    tid = cli.new_task("run me without cache accounting")
    r = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "agent.cli", "--config", str(silent), "run", tid],
        cwd=str(ROOT), env=cli.env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    assert r.returncode == 0, f"{r.stdout[-800:]}\n{r.stderr[-800:]}"
    assert "cache hit n/a" in r.stdout, r.stdout[-400:]


def test_run_refuses_when_a_plan_is_required_and_none_is_approved(cli, tmp_path):
    import yaml

    raw = yaml.safe_load(cli.config_path.read_text(encoding="utf-8"))
    raw["autonomy"]["plan_required"] = True
    strict = tmp_path / "config.strict.yaml"
    strict.write_text(yaml.safe_dump(raw), encoding="utf-8")
    tid = cli.new_task("unplanned")
    r = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "agent.cli", "--config", str(strict), "run", tid],
        cwd=str(ROOT), env=cli.env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    assert r.returncode == 1, r.stdout[-600:]
    assert "approve a plan first" in r.stdout


def test_status(cli):
    r = cli("status")
    assert cli.seeded_task[:14] in r.stdout


def test_approvals_lists_what_is_pending(cli):
    r = cli("approvals")
    assert cli.approval_id in r.stdout


def test_approvals_decide_is_final(cli):
    tid = cli.new_task("decide me")
    c = cli.conn()
    c.execute(
        """INSERT INTO approvals(id, task_id, step_id, tool, args_json,
                                 preview, reason, status, decided_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("a_twice", tid, "0", "write_file", "{}", "work/x", "seed",
         "pending", None, time.time()))
    c.commit()
    c.close()
    cli("approvals", "--decide", "a_twice", "--yes")
    cli("approvals", "--decide", "a_twice")
    c = cli.conn()
    status = c.execute("SELECT status FROM approvals WHERE id=?",
                       ("a_twice",)).fetchone()[0]
    c.close()
    assert status == "approved", f"a second decision flipped the row to {status}"


def test_alerts(cli):
    r = cli("alerts")
    assert "integrity: all chains verify against the anchor" in r.stdout


def test_verify(cli):
    assert cli("verify").stdout == cli("alerts").stdout


def test_tui_starts_and_keeps_running(cli):
    pytest.importorskip("textual")
    from agent.proc import kill_tree

    p = cli.spawn("tui", cli.seeded_task)
    try:
        time.sleep(6)
        if p.poll() is not None:
            out, err = p.communicate(timeout=30)
            pytest.fail(f"`bucephalus tui` exited {p.returncode}\n"
                        f"{out[-800:]}\n{err[-800:]}")
    finally:
        kill_tree(p.pid)
        try:
            p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()


def test_memory_lists_what_it_holds(cli):
    r = cli("memory")
    assert "prefers metric units" in r.stdout


def test_memory_forget_removes_and_says_so(cli):
    from agent.kernel.types import Taint
    from agent.memory.store import MemoryStore

    MemoryStore(cli.root / "memory").write("user", "drinks coffee at 3pm",
                                           Taint.USER)
    r = cli("memory", "--forget", "coffee")
    assert "forgotten" in r.stdout
    assert "coffee" not in MemoryStore(cli.root / "memory").read_raw("user")


def test_memory_accept_re_blesses_the_files(cli):
    from agent.memory.store import MemoryStore, check_fingerprint

    path = MemoryStore(cli.root / "memory").path("user")
    path.write_text(path.read_text(encoding="utf-8") + "- hand edited [user]\n",
                    encoding="utf-8")
    before = check_fingerprint(MemoryStore(cli.root / "memory"))
    cli("memory", "--accept")
    after = check_fingerprint(MemoryStore(cli.root / "memory"))
    assert (before, after) == ("tampered", "intact"), (before, after)


def test_memory_consolidate(cli):
    r = cli("memory", "--consolidate")
    assert "entries" in r.stdout and "->" in r.stdout


def test_search_rebuild_then_find(cli):
    cli("search", "--rebuild")
    r = cli("search", "seeded")
    assert cli.seeded_task[:12] in r.stdout, r.stdout[-600:]


def test_search_verify_reports_agreement(cli):
    cli("search", "--rebuild")
    r = cli("search", "--verify")
    assert json.loads(r.stdout)["consistent"] is True


def test_search_with_no_query_is_exit_2_not_a_crash(cli):
    r = cli("search", expect=2)
    assert "nothing to search for" in r.stdout


def test_report_writes_the_json_and_the_csv(cli):
    cli("report", cli.seeded_task)
    out = cli.root / "runs"
    assert (out / f"{cli.seeded_task}.json").exists()
    assert (out / f"{cli.seeded_task}_calls.csv").exists()


def test_pareto(cli):
    r = cli("pareto")
    assert "pass rate" in r.stdout


def test_skills_lists_the_store(cli):
    r = cli("skills")
    assert "units" in r.stdout and cli.skill_id[:12] in r.stdout


def test_skills_add_stores_a_candidate_and_warns_about_provenance(cli):
    r = cli("skills", "--add", str(cli.root / "a_skill.md"), "--name", "added")
    assert "as a candidate" in r.stdout
    assert "refuses every paired trial" in r.stdout, (
        "an undeclared drafting set must be stated when the skill is created")
    c = cli.conn()
    n = c.execute("SELECT COUNT(*) FROM skills WHERE name='added'").fetchone()[0]
    c.close()
    assert n == 1


def test_skills_add_of_a_missing_file_is_exit_1(cli):
    r = cli("skills", "--add", str(cli.root / "nope.md"), expect=1)
    assert "no such file" in r.stdout


def test_skills_declare_drafted_from(cli):
    r = cli("skills", "--declare-drafted-from", cli.skill_id, "none")
    assert "drafted_from=[]" in r.stdout


def test_skills_gate_refuses_without_trials(cli):
    r = cli("skills", "--gate", cli.skill_id)
    assert json.loads(r.stdout)["verdict"] != "promote"


def test_skills_decay(cli):
    cli("skills", "--decay")


def test_steer_forks_and_never_mutates(cli):
    tid = cli.new_task("steer me")
    r = cli("steer", tid, "use metric units")
    child = r.stdout.split("forked into ", 1)[1].split(";")[0].strip()
    c = cli.conn()
    parent_status = c.execute("SELECT status FROM tasks WHERE id=?",
                              (tid,)).fetchone()[0]
    goal = c.execute("SELECT contract_json FROM tasks WHERE id=?",
                     (child,)).fetchone()[0]
    c.close()
    assert parent_status == "paused"
    assert "[steered] use metric units" in json.loads(goal)["goal"]


def test_rewind_lists_and_goes_back(cli, monkeypatch):
    from agent.context import AgentContext
    from agent.execute.loop import run_task

    tid = cli.new_task("rewind me")
    cli("rewind", tid, "--list", expect=1)
    monkeypatch.setenv("BUCEPHALUS_DATA_DIR", str(cli.root))
    monkeypatch.setenv("BUCEPHALUS_CONFIG_DIR", str(cli.root))
    ctx = AgentContext.open(cli.config_path)
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.backends["cloud"].queue('<action tool="write_file">{"path": "work/r.txt", "content": "x"}</action>',
                                '<final>{"answer": "ok"}</final>')
    run_task(ctx, tid)
    ctx.close()
    listing = cli("rewind", tid, "--list").stdout
    assert "step 0" in listing
    r = cli("rewind", tid, "--step", "0", "--what", "both")
    assert "rewound" in r.stdout
    c = cli.conn()
    status = c.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    c.close()
    assert status == "paused"
    assert "no checkpoint at step 99" in cli("rewind", tid, "--step", "99", expect=1).stdout


def test_say_reaches_a_task_and_is_refused_for_a_finished_one(cli):
    tid = cli.new_task("tell me")
    assert "sent" in cli("say", tid, "use", "metric", "units").stdout
    assert "finished" in cli("say", cli.seeded_task, "late", expect=1).stdout


def test_bench_local(cli):
    r = cli("bench-local")
    assert "mock-local" in r.stdout and "mock-laptop" in r.stdout


def test_monitor(cli):
    r = cli("monitor", cli.seeded_task)
    assert "finding(s) written to monitor_flags" in r.stdout


def test_schedule_list(cli):
    r = cli("schedule", "list")
    assert cli.schedule_id in r.stdout


def test_schedule_add_then_remove(cli):
    r = cli("schedule", "add", "*/5 * * * *", "a scheduled goal")
    sid = r.stdout.split()[0]
    assert cli("schedule", "list").stdout.count(sid) == 1
    cli("schedule", "remove", "--id", sid)
    assert sid not in cli("schedule", "list").stdout


def test_schedule_add_without_a_spec_is_exit_2(cli):
    r = cli("schedule", "add", expect=2)
    assert "usage: bucephalus schedule add" in r.stdout


def test_schedule_remove_of_nothing_is_exit_1(cli):
    r = cli("schedule", "remove", "--id", "no_such_schedule", expect=1)
    assert "no such active schedule" in r.stdout


def test_supervise_once(cli):
    r = cli("supervise", "--once", "--tick", "0.1", timeout=300)
    assert "supervising" in r.stdout


def test_notifications(cli):
    r = cli("notifications")
    assert "nothing new" in r.stdout or "approvals --decide" in r.stdout


def test_lineage(cli):
    r = cli("lineage", cli.seeded_task)
    assert "open" in r.stdout or "closed" in r.stdout


def test_lineage_of_a_task_with_no_sessions_is_exit_1(cli):
    tid = cli.new_task("never run")
    r = cli("lineage", tid, expect=1)
    assert "no sessions" in r.stdout


def test_snapshot_is_wal_safe(cli):
    import sqlite3

    dest = cli.root / "evidence.db"
    cli("snapshot", str(dest))
    assert dest.exists()
    c = sqlite3.connect(dest)
    n = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    c.close()
    assert n > 0, "the snapshot has no tasks: the WAL did not travel"


def test_channel_refuses_while_disabled(cli):
    r = cli("channel", expect=2)
    assert "enabled is false" in r.stdout


def test_serve_binds_and_answers_health(cli):
    import urllib.request

    from agent.proc import kill_tree
    from conftest import serve_on_free_port

    def health(port):
        try:
            with urllib.request.urlopen(  # noqa: S310
                    f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                return json.loads(resp.read().decode())
        except Exception:  # noqa: BLE001
            return None

    p, port = serve_on_free_port(lambda port: cli.spawn("serve", "--port", str(port)),
                                 lambda port: health(port) is not None, timeout=90)
    try:
        body = health(port)
        assert body.get("ok") is True and "backends" in body, body
    finally:
        kill_tree(p.pid)
        try:
            p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()


def test_every_subcommand_is_entered_by_a_test_here():
    from agent.cli import build_parser

    subparsers = [a for a in build_parser()._actions  # noqa: SLF001
                  if isinstance(a, argparse._SubParsersAction)]  # noqa: SLF001
    declared = set(subparsers[0].choices)
    source = Path(__file__).read_text(encoding="utf-8")
    entered = {c for c in declared
               if f'cli("{c}"' in source or f'cli.spawn("{c}"' in source
               or f'"-m", "agent.cli", "--config", str(strict), "{c}"' in source}
    assert declared == entered, f"no test enters: {sorted(declared - entered)}"


def test_exec_runs_one_task_and_streams_json(cli):
    r = cli("exec", "say hello", "--scope", "work", "--output-format", "stream-json", "--approve-plan")
    rows = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    assert rows[0]["type"] == "task.created" and rows[-1]["type"] == "result" and rows[-1]["exit_code"] == 0
