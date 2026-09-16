"""The pile, fixed - one test per defect, 2026-09-16.

Each of these closes a DEFERRED entry that had been carried for weeks. Where
the defect lives in a measurement script that no unit test can drive cheaply,
the check reads the source, the way `test_runlock.py` and
`test_stall_injection.py` already do: the alternative is an hours-long run.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOISE = (ROOT / "bench" / "noise.py").read_text(encoding="utf-8")


def test_d15_the_archived_test_writes_its_output_under_tmp_path():
    """D15. `_FakeCtx(profile)` left `tmp_path` None, so `resolve()` fell back
    to `Path(".")` and `cmd_paired` wrote cal.json into the repository root -
    on every suite run, including one at 08:44 on 2026-09-16."""
    src = (ROOT / "tests" / "archive" / "test_bench_suite.py").read_text(encoding="utf-8")
    assert '_FakeCtx(profile or "mixed", tmp_path)' in src
    assert '_FakeCtx(profile or "mixed")\n' not in src


def test_d9_the_limit_flag_says_when_it_takes_its_tasks():
    """D9. It takes the first N in SUITE order and shuffles after, so on a
    randomised run `--limit` is not "the first N you will see"."""
    assert "SUITE ORDER" in NOISE and "before the " in NOISE


def test_d11_a_run_without_a_median_still_has_a_ceiling():
    """D11. Tasks 1-3 could never be flagged: M18 took 1777.4 s as task 2 and
    passed unflagged."""
    assert "WALL_OUTLIER_ABS_S" in NOISE
    assert 'out["wall_outlier"] = out["wall_s"] > WALL_OUTLIER_ABS_S' in NOISE


def test_d36_the_run_record_carries_the_unreturned_count():
    """Found by the verification run: `n_unreturned` stopped at the database,
    so a stalled call would be in `calls` and invisible in the report."""
    assert '"n_calls": m.n_calls,' in NOISE
    assert '"n_unreturned": m.n_unreturned,' in NOISE


def test_d1_private_commit_is_recorded_beside_working_set():
    """D1. Windows trims the working set under pressure, so it read 2.28 ->
    3.251 GB across a successful reload - the trim, not the leak."""
    from agent.proc import worker_private_gb, worker_rss_gb

    assert '"worker_private_after_gb": worker_private_gb(),' in NOISE
    for value in (worker_rss_gb(), worker_private_gb()):
        assert value is None or value >= 0.0     # no worker running here


def test_d10_the_user_facing_stop_goes_through_kill_tree():
    """D10. `Stop-Process` kills the venv trampoline and leaves the real
    interpreter running (C13) - four occurrences, including a manual stop that
    silently did nothing while the ledger kept growing."""
    ps1 = (ROOT / "measure.ps1").read_text(encoding="utf-8")
    assert "kill_tree" in ps1, "the stop path does not route through kill_tree"
    assert "Stop-Process -Id ([int]$running)" not in ps1


def test_d3_the_robots_fetch_is_bounded():
    """D3's sweep. Every other blocking path streams with a cap; a plain `get`
    reads until the server stops, and httpx's read timeout measures the gap
    BETWEEN bytes - the shape that let a 600 s timeout run 2539 s."""
    import agent.net.politeness as pol

    src = (ROOT / "src" / "agent" / "net" / "politeness.py").read_text(encoding="utf-8")
    assert "httpx.stream(" in src and "ROBOTS_MAX_BYTES" in src

    read = []

    class _R:
        status_code = 200

        def iter_text(self):
            for _ in range(200):              # a server with far more to give
                read.append(1)
                yield "User-agent: *" + chr(10) + "Disallow:" + chr(10) + "x" * (1024 * 1024)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    pol.reset()
    orig = pol.httpx.stream
    pol.httpx.stream = lambda *a, **k: _R()
    try:
        pol._robots_for("https://example.test/page")  # noqa: SLF001
    finally:
        pol.httpx.stream = orig
        pol.reset()
    megabytes = sum(read)
    assert megabytes <= 2, f"read {megabytes} MB of robots.txt before stopping"


def test_d7_the_run_sheet_shows_what_was_never_scored():
    """D7. `noise.py` emitted both keys and the sheet rendered neither, so
    "30 tasks" and "30 attempted, 2 never scored" read identically."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("bench_report_d7", ROOT / "bench" / "report.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    out = mod.render({"suite": "s", "n_tasks": 30, "runs_per_task": 3,
                      "mean_pass_rate": 0.5, "stable_pass": 20, "stable_fail": 8,
                      "flaky": 2, "machine_fault_keys": ["H23"],
                      "excluded_keys": ["M11", "E02"]}, None)
    assert "EXCLUDED" in out and "M11, E02" in out, out
    assert "machine faults, recovered" in out and "H23" in out


def test_d6_the_estimator_is_recorded_beside_the_servers_count(ctx, task, scripted):
    """D6. The context ceiling budgets against the local estimator, and its
    error was unmeasurable without an API key until C62. Now every step call
    records what the estimator thought, beside what the backend reports."""
    from agent.execute.loop import run_task

    scripted("cloud", '<final>{"answer": "done"}</final>')
    run_task(ctx, task.id)

    called = [json.loads(p) for (p,) in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='model.called'",
        (task.id,))]
    assert called and all(e.get("est_input_tokens", 0) > 0 for e in called), called
    reported = ctx.conn.execute(
        "SELECT input_tokens FROM calls WHERE task_id=? ORDER BY id", (task.id,)
    ).fetchone()[0]
    assert reported > 0        # both halves of the comparison now exist


def test_g8_an_external_skill_is_imported_as_an_untrusted_candidate(ctx):
    """Phase 12 is withdrawn (FINDINGS 1.29), but G8's claim - a portable
    format - can still be shown: a skill authored elsewhere enters the store
    as an untrusted candidate and is gated like anything else."""
    from agent.skills.gate import ContaminationError, check_contamination
    from agent.skills.store import import_external_skill

    body = "# borrowed\nwhen converting units\n\n## step\nconvert before computing\n"
    sid = import_external_skill(ctx.skills, "borrowed", body,
                                "https://example.test/skills/borrowed.md")
    meta = ctx.skills.load(sid, verify=True)
    assert meta["status"] == "candidate", "an imported skill is never live"
    assert meta["provenance"]["drafted_by"] == "external"
    assert meta["provenance"]["trust"] == "untrusted"
    assert meta["provenance"]["sources"][0]["url"].startswith("https://example.test")
    assert meta["body"] == body, "the format crossed the boundary unchanged"

    try:
        check_contamination(ctx, sid, "E01")
    except ContaminationError as e:
        assert "drafting set" in str(e)      # undeclared: no trial may run
    else:
        raise AssertionError("an undeclared drafting set must refuse every trial")
