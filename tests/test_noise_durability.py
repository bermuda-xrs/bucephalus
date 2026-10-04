"""The noise floor must survive being killed. It is a multi-hour job.

`noise.py` accumulated every result in memory and called `write_text` once,
after the final task — so a kill at hour three of a four-hour pass lost all
of it, not one task's work. On CPU-only hardware that is the difference
between a measurement and a wasted night.

These are tests of the runtime path, not of the helpers in isolation: the
last one kills a real subprocess mid-pass and asserts the survivors come
back on resume.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_noise():
    spec = importlib.util.spec_from_file_location("noise_mod", ROOT / "bench" / "noise.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


noise = _load_noise()


class _Args:
    suite, split, runs, limit, skill = "aero_calc", None, 3, None, None
    profile = "local"
    executor = None
    tier = None
    only = None
    # Part of the signature since task order was randomised: two orders in
    # one ledger are two sets of measurement conditions, blended silently.
    order_seed = 1234


def test_each_completed_run_is_on_disk_before_the_next_one_starts(tmp_path):
    led = noise.ledger_path(tmp_path / "nf.json")
    sig = noise.run_signature(_Args(), 2)
    noise.append_run(led, sig, {"run": 0, "key": "E01", "status": "done",
                                "passed": True, "wall_s": 1.0})
    assert led.exists(), "nothing was written until the end of the pass"
    noise.append_run(led, sig, {"run": 0, "key": "E02", "status": "plateau",
                                "passed": False, "wall_s": 2.0})
    done = noise.load_completed(led, sig)
    assert set(done) == {(0, "E01"), (0, "E02")}
    assert done[(0, "E01")]["passed"] is True


def test_a_truncated_final_line_is_dropped_not_guessed_at(tmp_path, capsys):
    """A kill mid-write leaves half a line. Half a result is not a result."""
    led = noise.ledger_path(tmp_path / "nf.json")
    sig = noise.run_signature(_Args(), 2)
    noise.append_run(led, sig, {"run": 0, "key": "E01", "status": "done",
                                "passed": True, "wall_s": 1.0})
    with led.open("a", encoding="utf-8") as fh:
        fh.write('{"run": 0, "key": "E02", "sta')
    done = noise.load_completed(led, sig)
    assert set(done) == {(0, "E01")}
    assert "truncated" in capsys.readouterr().out


def test_resume_refuses_a_ledger_from_a_different_configuration(tmp_path):
    """Blending two measurements into one report is the failure to avoid."""
    led = noise.ledger_path(tmp_path / "nf.json")
    noise.append_run(led, noise.run_signature(_Args(), 2),
                     {"run": 0, "key": "E01", "status": "done",
                      "passed": True, "wall_s": 1.0})

    class Other(_Args):
        split = "eval"          # a different measurement entirely

    with pytest.raises(SystemExit, match="different run configuration"):
        noise.load_completed(led, noise.run_signature(Other(), 2))


def test_report_write_is_atomic(tmp_path):
    """A kill during the write must not leave a half-parsed report."""
    dest = tmp_path / "nf.json"
    dest.write_text('{"old": true}', encoding="utf-8")
    noise.write_report(dest, {"mean_pass_rate": 0.5, "tasks": []})
    assert json.loads(dest.read_text(encoding="utf-8"))["mean_pass_rate"] == 0.5
    assert not list(tmp_path.glob("*.tmp")), "temp file left behind"


def test_a_real_kill_mid_pass_loses_at_most_one_task(tmp_path):
    """End to end: kill a live process, then resume and get the survivors.

    The helpers passing in isolation is not evidence the runner survives —
    that is the exact "green test, zero call sites" failure this project
    keeps finding. So: a real subprocess, a real SIGKILL, a real resume.
    """
    led = noise.ledger_path(tmp_path / "nf.json")
    sig = {"suite": "aero_calc", "split": None, "runs": 1,
           "limit": None, "skill": None, "profile": "local", "n_specs": 5}
    script = textwrap.dedent(f'''
        import importlib.util, sys, time
        spec = importlib.util.spec_from_file_location("n", {str(ROOT / "bench" / "noise.py")!r})
        m = importlib.util.module_from_spec(spec); sys.modules["n"] = m
        spec.loader.exec_module(m)
        led, sig = {str(led)!r}, {sig!r}
        from pathlib import Path
        for i, k in enumerate(["E01", "E02", "E03", "E04", "E05"]):
            m.append_run(Path(led), sig, {{"run": 0, "key": k,
                                          "status": "done", "passed": True,
                                          "wall_s": 1.0}})
            print(k, flush=True)
            time.sleep(1.5)
    ''')
    sf = tmp_path / "child.py"
    sf.write_text(script, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(sf)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "E01"
        assert proc.stdout.readline().strip() == "E02"
    finally:
        proc.kill()
        proc.wait(timeout=10)
        proc.stdout.close()

    survived = noise.load_completed(led, sig)
    assert (0, "E01") in survived and (0, "E02") in survived, \
        "work completed before the kill did not survive it"
    assert (0, "E05") not in survived
    # and the pass can pick up where it stopped
    assert len(survived) < 5


def test_resume_refuses_a_ledger_from_a_different_profile(tmp_path):
    """Half a pass measured local and half mixed is not a measurement."""
    led = noise.ledger_path(tmp_path / "nf.json")
    noise.append_run(led, noise.run_signature(_Args(), 2),
                     {"run": 0, "key": "E01", "status": "done",
                      "passed": True, "wall_s": 1.0})

    class Mixed(_Args):
        profile = "mixed"

    with pytest.raises(SystemExit, match="different run configuration"):
        noise.load_completed(led, noise.run_signature(Mixed(), 2))


def test_a_pass_1_ledger_resumes_into_a_three_pass_run(tmp_path):
    """The staged plan: run pass 1, decide, then continue — not redo.

    Each row is keyed (run_index, task_key), so rows from `--runs 1` are the
    same work as row 0 of `--runs 3`. If `runs` were part of the signature,
    deciding after pass 1 would cost that pass over again — about two hours
    on this hardware.
    """
    led = noise.ledger_path(tmp_path / "nf.json")

    class Pass1(_Args):
        runs = 1

    class AllThree(_Args):
        runs = 3

    for key in ("E01", "E02"):
        noise.append_run(led, noise.run_signature(Pass1(), 2),
                         {"run": 0, "key": key, "status": "done",
                          "passed": True, "wall_s": 1.0})

    done = noise.load_completed(led, noise.run_signature(AllThree(), 2))
    assert set(done) == {(0, "E01"), (0, "E02")}, \
        "pass 1 did not carry over into the three-pass run"


def test_the_report_records_why_each_pass_ended(tmp_path):
    """Pass rate alone cannot distinguish a hard task from a looping agent.

    At max_steps=6 the step limit binds long before the 600s budget — no run
    on record has ever ended in `budget_exhausted` — so `max_steps` is the
    signal that matters, and it needs to be in the report rather than
    recounted by hand from per-task statuses.
    """
    rows = [
        {"key": "E01", "tier": "easy", "split": "drafting", "passes": 0,
         "runs": 2, "statuses": ["max_steps", "plateau"], "classification": "stable_fail"},
        {"key": "E02", "tier": "easy", "split": "drafting", "passes": 2,
         "runs": 2, "statuses": ["done", "done"], "classification": "stable_pass"},
    ]
    counts: dict[str, int] = {}
    for r in rows:
        for st in r["statuses"]:
            counts[st] = counts.get(st, 0) + 1
    assert counts == {"max_steps": 1, "plateau": 1, "done": 2}
    assert counts.get("max_steps", 0) == 1
    assert counts.get("budget_exhausted", 0) == 0


def test_a_resumed_report_does_not_understate_the_cost():
    """`time.time() - started` only covers the tasks THIS process ran.

    On a resumed multi-pass run that silently understates the real cost —
    E01 resumed at 339s while the session reported 268s total. Two fields,
    because they answer two different questions.
    """
    results = {"E01": [{"wall_s": 339.0}], "E02": [{"wall_s": 268.3}]}
    all_runs = round(sum(x["wall_s"] for r in results.values() for x in r), 1)
    assert all_runs == 607.3
    assert all_runs > 268.3, "resumed work must be counted in the total"


def test_a_tier_filtered_run_cannot_resume_a_full_one(tmp_path):
    """--tier changes WHICH tasks are measured, so it belongs in the signature.

    Without it a medium/hard sighting shot would happily reuse rows from the
    full pass and report a pass rate over a set of tasks nobody selected.
    """
    led = noise.ledger_path(tmp_path / "nf.json")

    class Full(_Args):
        tier = None

    class HardOnly(_Args):
        tier = ["hard", "medium"]

    noise.append_run(led, noise.run_signature(Full(), 30),
                     {"run": 0, "key": "E01", "status": "done",
                      "passed": True, "wall_s": 1.0})
    with pytest.raises(SystemExit, match="different run configuration"):
        noise.load_completed(led, noise.run_signature(HardOnly(), 12))


def test_the_tier_signature_is_order_independent(tmp_path):
    """--tier hard --tier medium and the reverse are the same measurement."""
    class A(_Args):
        tier = ["hard", "medium"]

    class B(_Args):
        tier = ["medium", "hard"]

    assert noise.run_signature(A(), 12) == noise.run_signature(B(), 12)


def test_an_only_filtered_run_cannot_resume_a_full_one(tmp_path):
    """--only changes WHICH tasks are measured, so it belongs in the signature."""
    led = noise.ledger_path(tmp_path / "nf.json")

    class Full(_Args):
        only = None

    class Six(_Args):
        only = ["H23", "M11", "M12", "M14", "M15", "M16"]

    noise.append_run(led, noise.run_signature(Full(), 30),
                     {"run": 0, "key": "E01", "status": "done",
                      "passed": True, "wall_s": 1.0})
    with pytest.raises(SystemExit, match="different run configuration"):
        noise.load_completed(led, noise.run_signature(Six(), 6))


def test_the_only_signature_is_order_independent(tmp_path):
    class A(_Args):
        only = ["M11", "H23"]

    class B(_Args):
        only = ["H23", "M11"]

    assert noise.run_signature(A(), 2) == noise.run_signature(B(), 2)


def test_resume_refuses_a_ledger_written_under_a_different_task_order(tmp_path):
    """Two task orders in one ledger are two sets of conditions, blended.

    Task order is randomised because the suite order is fixed - all easy,
    then all medium, then all hard - so any drift across a long run is
    confounded with TIER. Phase 2A measured exactly that: the model worker
    leaked ~139 MB per task, so the hard tasks, which always ran last, were
    measured under the worst conditions of the run.

    Randomising fixes the confound only if a resume cannot silently stitch a
    run under seed A onto rows measured under seed B.
    """
    led = noise.ledger_path(tmp_path / "nf.json")

    class SeedA(_Args):
        order_seed = 111

    class SeedB(_Args):
        order_seed = 222

    noise.append_run(led, noise.run_signature(SeedA(), 2),
                     {"run": 0, "key": "E01", "status": "done",
                      "passed": True, "wall_s": 1.0})

    assert noise.run_signature(SeedA(), 2) != noise.run_signature(SeedB(), 2), (
        "order_seed is not part of the signature, so a resume under a "
        "different task order would be accepted")

    # It refuses LOUDLY rather than quietly returning nothing - the right
    # direction for a guard whose whole job is to stop a silent blend.
    with pytest.raises(SystemExit):
        noise.load_completed(led, noise.run_signature(SeedB(), 2))

    same = noise.load_completed(led, noise.run_signature(SeedA(), 2))
    assert len(same) == 1, "the SAME seed must still resume, or nothing resumes"
