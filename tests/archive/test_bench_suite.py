"""The benchmark's own answers are verified here, not asserted.

Trap 7 in docs/HANDOVER.md section 19: H27 was unsolvable as first generated because nobody
independently recomputed it. H30 then shipped an answer that depended on an
unstated convention, so two correct solvers disagreed by 1.27% and the
continuous scorer ranked one of them at 0.37. Both failures share a cause —
the expected value lived only in a table, with no second computation to
disagree with it.

So: an independent solver for the tasks whose answers turn on a modelling
choice, plus a check that the emitted tree is not stale relative to the table.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from math import sqrt
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TREE = ROOT / "bench" / "tasks" / "archive" / "aero_calc"


def _load_suite():
    spec = importlib.util.spec_from_file_location(
        "aero_calc_suite", ROOT / "bench" / "suites" / "archive" / "aero_calc.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations via sys.modules[cls.__module__]; without
    # this registration it raises AttributeError on a None module.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


suite = _load_suite()
BY_ID = {t.id: t for t in suite.ALL}


def _h30_reference() -> dict[str, float]:
    """H30, recomputed from the task's own inputs. Independent of the table.

    Patched-conic Hohmann, both planetary orbits circular at r1/r2 — the
    convention the objective pins. Deliberately written out longhand rather
    than calling anything the suite ships, because the point is disagreement.
    """
    c = suite.CONSTANTS
    p = BY_ID["H30"].params
    mu_sun = c[p["primary"]]["mu"]
    r1, r2 = p["r1_m"], p["r2_m"]
    a = (r1 + r2) / 2

    dep, cap = c[p["depart_body"]], c[p["capture_body"]]
    r_park = dep["radius"] + p["depart_altitude_km"] * 1000.0
    r_capt = cap["radius"] + p["capture_altitude_km"] * 1000.0

    # Heliocentric leg: circular orbits at r1/r2, transfer ellipse between.
    v_inf_out = sqrt(mu_sun * (2 / r1 - 1 / a)) - sqrt(mu_sun / r1)
    v_inf_in = sqrt(mu_sun / r2) - sqrt(mu_sun * (2 / r2 - 1 / a))

    # Planetocentric legs: hyperbola at periapsis versus the circular orbit.
    burn_out = sqrt(v_inf_out**2 + 2 * dep["mu"] / r_park) - sqrt(dep["mu"] / r_park)
    burn_in = sqrt(v_inf_in**2 + 2 * cap["mu"] / r_capt) - sqrt(cap["mu"] / r_capt)

    return {
        "delta_v_departure_m_s": burn_out,
        "delta_v_capture_m_s": burn_in,
        "delta_v_total_m_s": burn_out + burn_in,
    }


def test_h30_expected_values_are_independently_reproducible():
    """The table agrees with a solver that never saw it, to its own rounding."""
    for key, ref in _h30_reference().items():
        assert BY_ID["H30"].expected[key] == pytest.approx(ref, abs=0.05), key


def test_h30_objective_pins_the_heliocentric_speed_convention():
    """Without this sentence the task has two correct answers 1.27% apart.

    constants.json gives mars.orbital_v = 24077 (the true mean speed of an
    eccentric orbit) while sqrt(mu_sun/r2) = 24131.5 (a circular orbit at r2).
    Deleting the pin re-opens the ambiguity, and it re-opens silently: both
    answers still pass at tol 2%, one just scores 0.37.
    """
    detail = BY_ID["H30"].detail
    assert "sqrt(mu_sun / r)" in detail
    assert "orbital_v" in detail
    emitted = (TREE / "eval" / "H30" / "task.yaml").read_text(encoding="utf-8")
    assert "sqrt(mu_sun / r)" in emitted


def test_a_spec_following_solver_scores_full_marks_on_h30():
    """The defect that prompted the change: a right answer scored 0.37.

    The ratchet keeps a revision only if it scores strictly better, so a
    correct answer ranked at 0.37 makes the ratchet prefer noise.
    """
    verify = TREE / "eval" / "H30" / "verify.py"
    ns: dict = {}
    exec(compile(verify.read_text(encoding="utf-8"), str(verify), "exec"), ns)

    scores = []
    for key, got in _h30_reference().items():
        exp = ns["EXPECTED"][key]
        rel = abs(got - exp) / abs(exp)
        scores.append(max(0.0, 1.0 - rel / ns["TOL"]))
    assert all(s > 0.0 for s in scores), "a correct answer must pass"
    assert sum(scores) / len(scores) > 0.99, "a correct answer must score ~1.0"


def test_emitted_tree_matches_the_table():
    """Catches an edited table that was never regenerated.

    The tree is generated output checked into git, so it can silently drift
    from its source. Everything downstream — the DB suite, the noise floor,
    the gate — reads the tree.
    """
    stale = []
    for t in suite.ALL:
        verify = TREE / t.split / t.id / "verify.py"
        assert verify.exists(), f"{t.id} missing from the emitted tree"
        ns: dict = {}
        exec(compile(verify.read_text(encoding="utf-8"), str(verify), "exec"), ns)
        if ns["EXPECTED"] != t.expected or ns["TOL"] != (t.tol or 1e-9):
            stale.append(t.id)
    assert not stale, (
        f"emitted tree is stale for {stale}; regenerate with "
        "`python bench/suites/archive/aero_calc.py --out bench/tasks/archive/aero_calc`"
    )


def test_every_eval_task_can_fail():
    """A verifier that cannot catch a wrong answer measures nothing.

    Section 18 of docs/HANDOVER.md: this is the failure an audit found in 59% of SWE-bench
    Verified's hardest tasks. Feed each verifier a deliberately wrong answer
    and require it to say no.
    """
    for t in suite.ALL:
        d = TREE / t.split / t.id
        ns: dict = {}
        exec(compile((d / "verify.py").read_text(encoding="utf-8"),
                     str(d / "verify.py"), "exec"), ns)
        wrong = {
            k: ([-1] if isinstance(v, list) else float(v) * 3.0 + 1.0)
            for k, v in t.expected.items()
        }
        import os
        cwd = os.getcwd()
        os.chdir(d)
        try:
            (d / "output").mkdir(exist_ok=True)
            answer = d / "output" / "answer.json"
            answer.write_text(json.dumps(wrong), encoding="utf-8")
            passed, score = ns["check"]()
        finally:
            answer.unlink(missing_ok=True)
            os.chdir(cwd)
        assert not passed, f"{t.id} passed a deliberately wrong answer"
        assert score == 0.0 or score < 0.5, f"{t.id} scored {score} on garbage"


# ---------------------------------------------------------------------------
# The paired-trial harness. Flags that exist but are not honoured are worse
# than no flags: the run still happens and the label on it is wrong.
# ---------------------------------------------------------------------------


def _load_bench_run():
    spec = importlib.util.spec_from_file_location(
        "bench_run", ROOT / "bench" / "run.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeCtx:
    """Just enough context for cmd_paired's control flow."""

    def __init__(self, profile, tmp_path=None):
        self.config = type("C", (), {
            "profile": profile, "profile_provenance": {},
            "gate": type("G", (), {"min_paired_trials": 30,
                                   "min_discordant_pairs": 10,
                                   "min_lift_pp": 5.0,
                                   "max_p_value": 0.05})(),
            "resolve": staticmethod(
                lambda x: str((tmp_path or Path(".")) / Path(x).name)),
        })()
        self.config.autonomy = type("A", (), {
            "auto_review": True, "approve_scoped_writes": False})()
        self.backends = {"cloud": type("B", (), {
            "cfg": type("BC", (), {"model": "mock"})()})()}
        self.closed = False

    def close(self):
        self.closed = True


def _fake_specs():
    return [
        {"key": k, "goal": f"goal {k}", "success_criterion": "verify_program",
         "output_schema": {"x-split": split}, "artifact_scope": [f"work/{k}"]}
        for k, split in (("E01", "drafting"), ("E02", "drafting"),
                         ("E06", "eval"), ("E07", "eval"))
    ]


def test_paired_enforces_the_declared_profile(monkeypatch, capsys, tmp_path):
    """It defaulted to profile=mixed while every other entry point ran local.

    `Config.profile` defaults to "mixed" and config.local.yaml does not set the
    key, so the most important measurement in the project was the one not
    checking what it was allowed to touch — and it recorded "mixed" in the
    provenance of a run that was in fact local.
    """
    mod = _load_bench_run()
    seen: dict = {}

    class FakeAgentContext:
        @staticmethod
        def open(config, profile=None):
            seen["config"], seen["profile"] = config, profile
            return _FakeCtx(profile or "mixed")

    monkeypatch.setattr(mod, "AgentContext", FakeAgentContext)
    monkeypatch.setattr(mod, "load_suite", lambda spec, ctx=None: _fake_specs())
    monkeypatch.setattr(mod, "run_paired_trial", lambda *a, **k: None)
    monkeypatch.setattr(mod, "evaluate_skill",
                        lambda ctx, sid, task_keys=None: type(
                            "D", (), {"verdict": "insufficient",
                                      "reason": "stub", "detail": {}})())

    args = type("A", (), {"config": "config.local.yaml", "profile": "local",
                          "suite": "aero_calc", "split": None,
                          "skill_id": "abc", "resume": False, "auto_review": False,
                          "out": str(tmp_path / "cal.json")})()
    assert mod.cmd_paired(args) == 0
    assert seen["profile"] == "local", "the declared profile never reached the context"


def test_paired_split_filter_keeps_the_contamination_boundary(monkeypatch, capsys, tmp_path):
    """--split eval must actually drop the drafting tasks.

    check_contamination only catches a task named in the skill's provenance,
    and a hand-written skill names none — so for anything that could have been
    informed by the drafting half, the split filter is the only thing standing
    between the gate and a manufactured lift.
    """
    mod = _load_bench_run()
    ran: list[str] = []

    class FakeAgentContext:
        @staticmethod
        def open(config, profile=None):
            return _FakeCtx(profile or "mixed")

    monkeypatch.setattr(mod, "AgentContext", FakeAgentContext)
    monkeypatch.setattr(mod, "load_suite", lambda spec, ctx=None: _fake_specs())
    monkeypatch.setattr(mod, "run_paired_trial",
                        lambda ctx, sid, spec, seed=None: ran.append(spec.key))
    monkeypatch.setattr(mod, "evaluate_skill",
                        lambda ctx, sid, task_keys=None: type(
                            "D", (), {"verdict": "insufficient",
                                      "reason": "stub", "detail": {}})())

    args = type("A", (), {"config": "config.local.yaml", "profile": "local",
                          "suite": "aero_calc", "split": "eval",
                          "skill_id": "abc", "resume": False, "auto_review": False,
                          "out": str(tmp_path / "cal.json")})()
    assert mod.cmd_paired(args) == 0
    assert ran == ["E06", "E07"], f"drafting tasks leaked into the trial: {ran}"
    capsys.readouterr()
