"""M9 acceptance — the gate, including the calibration check.

The second test is the calibration check. Run it before trusting any
rejection: a gate that rejects a genuinely good skill is measuring your task
suite, not the skill.
"""

from __future__ import annotations

import hashlib

import pytest

from agent.skills.gate import (
    ContaminationError,
    TaskSpec,
    check_contamination,
    evaluate_skill,
    promote,
    run_paired_trial,
)

MAGIC = "MAGIC_PROCEDURE_v1"
GOOD_SKILL = (
    f"# answering with the checked value\n"
    f"when the task asks for a value ({MAGIC})\n\n"
    f"## procedure\n"
    f"Return exactly {{\"value\": 42}} and nothing else.\n"
)
USELESS_SKILL = (
    "# general advice\n"
    "when doing anything\n\n"
    "## procedure\n"
    "Try your best. Consider the context. Be thorough and accurate.\n"
)

SCHEMA = {"type": "object", "required": ["value"],
          "properties": {"value": {"type": "integer"}}}


def _specs(n: int = 30) -> list[TaskSpec]:
    return [
        TaskSpec(
            key=f"trial-{i}",
            goal=f"[trial-{i}] produce the checked value",
            success_criterion="json_schema",
            output_schema=SCHEMA,
            artifact_scope=["work"],
        )
        for i in range(n)
    ]


def _install_responder(ctx, mode: str) -> None:
    """A model whose behaviour depends on whether the skill is in the prefix."""

    def responder(prefix: str, tail: str) -> str:
        has_skill = MAGIC in prefix
        key = ""
        for token in prefix.split():
            if token.startswith("[trial-"):
                key = token.strip("[]\",")
        if mode == "good":
            return (
                '<final>{"value": 42}</final>'
                if has_skill
                else '<final>{"note": "no idea what is wanted"}</final>'
            )
        # "useless": outcome is noise that ignores the skill entirely
        seed = f"{key}:{'s' if has_skill else 'c'}".encode()
        wins = hashlib.sha256(seed).digest()[0] % 2 == 0
        return (
            '<final>{"value": 42}</final>' if wins
            else '<final>{"note": "wrong shape"}</final>'
        )

    for name in ("cloud", "local", "review"):
        ctx.backends[name]._responder = responder  # noqa: SLF001 - test seam


def test_gate_rejects_useless_skill(ctx, workspace):
    """Synthetic skill of pure filler text. 30 paired trials. Assert rejection."""
    ctx.config.limits.max_steps = 1
    _install_responder(ctx, "useless")
    # drafted_from=[]: a declaration, not a default. Since D29's sweep an
    # undeclared drafting set certifies no trial (test_empty_means_nothing).
    sid = ctx.skills.store("filler", USELESS_SKILL, {"applies_when": "anything"},
                           {"drafted_by": "test", "drafted_from": []})
    for i, spec in enumerate(_specs()):
        run_paired_trial(ctx, sid, spec, seed=i)

    decision = evaluate_skill(ctx, sid)
    assert decision.verdict in ("reject", "insufficient"), decision.reason
    if decision.verdict == "reject":
        assert decision.detail["lift_pp"] < 5.0


def test_gate_accepts_known_good_skill(ctx, workspace):
    """Calibration check. If this fails, the gate is too strict OR the suite
    is not sensitive enough — debug that before trusting any rejection."""
    ctx.config.limits.max_steps = 1
    _install_responder(ctx, "good")
    sid = ctx.skills.store("checked-value", GOOD_SKILL,
                           {"applies_when": "produce the checked value"},
                           {"drafted_by": "human", "drafted_from": []})
    for i, spec in enumerate(_specs()):
        run_paired_trial(ctx, sid, spec, seed=i)

    decision = evaluate_skill(ctx, sid)
    assert decision.verdict == "promote", decision.reason
    assert decision.detail["lift_pp"] > 5.0
    assert decision.detail["p"] < 0.05


def test_promotion_requires_a_human(ctx, workspace):
    """Only the gate promotes, and only with human approval (M8.4 rule 6)."""
    ctx.config.limits.max_steps = 1
    _install_responder(ctx, "good")
    sid = ctx.skills.store("checked-value", GOOD_SKILL,
                           {"applies_when": "produce the checked value"},
                           {"drafted_by": "human", "drafted_from": []})
    for i, spec in enumerate(_specs()):
        run_paired_trial(ctx, sid, spec, seed=i)
    decision = evaluate_skill(ctx, sid)

    with pytest.raises(PermissionError):
        promote(ctx, sid, decision, human_approved=False)
    assert ctx.skills.load(sid, verify=False)["status"] != "promoted"

    promote(ctx, sid, decision, human_approved=True)
    assert ctx.skills.load(sid, verify=False)["status"] == "promoted"


def test_insufficient_trials_is_not_a_pass(ctx, workspace):
    ctx.config.limits.max_steps = 1
    _install_responder(ctx, "good")
    sid = ctx.skills.store("thin", GOOD_SKILL, {"applies_when": "x"},
                           {"drafted_from": []})
    for i, spec in enumerate(_specs(5)):
        run_paired_trial(ctx, sid, spec, seed=i)
    d = evaluate_skill(ctx, sid)
    assert d.verdict == "insufficient"


def test_no_contamination(ctx):
    """The drafting task never appears in the trial set."""
    sid = ctx.skills.store(
        "grounded", GOOD_SKILL, {"applies_when": "x"},
        {"drafted_by": "local", "drafted_from": ["trial-0", "trial-1"]},
    )
    with pytest.raises(ContaminationError):
        check_contamination(ctx, sid, "trial-0")
    check_contamination(ctx, sid, "trial-99")  # clean key, no raise

    with pytest.raises(ContaminationError):
        run_paired_trial(ctx, sid, TaskSpec(key="trial-1", goal="g"))


def test_catastrophic_regression_is_a_veto(ctx, workspace):
    """A task control passed and skill failed is worse than neither passing."""
    import time

    sid = ctx.skills.store("risky", GOOD_SKILL, {"applies_when": "x"}, {})
    rows = []
    for i in range(30):
        # skill wins broadly, but destroys one important task
        ctrl, skl = (1, 0) if i == 0 else ((0, 1) if i < 20 else (0, 0))
        rows.append((f"k{i}", "control", ctrl))
        rows.append((f"k{i}", "skill", skl))
    for key, arm, success in rows:
        ctx.conn.execute(
            """INSERT INTO skill_trials(skill_id, task_key, arm, success, score,
                                        paid_tokens, free_tokens, wall_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (sid, key, arm, success, float(success), 0, 0, 0, time.time()),
        )
    ctx.conn.commit()

    assert evaluate_skill(ctx, sid).verdict == "promote"
    vetoed = evaluate_skill(ctx, sid, catastrophic_keys={"k0"})
    assert vetoed.verdict == "reject" and "catastrophic" in vetoed.reason


def test_decay_demotes_unused_skills(ctx):
    from agent.skills.gate import decay_pass

    sid = ctx.skills.store("stale", GOOD_SKILL, {"applies_when": "x"}, {})
    ctx.skills.set_status(sid, "promoted")
    actions = decay_pass(ctx)
    assert any(a["action"] == "demoted" and a["why"] == "unused" for a in actions)
    assert ctx.skills.load(sid, verify=False)["status"] == "demoted"


def test_promotion_rate_reports_candidates(ctx):
    """Moved from the retired generator's tests: the gate reports its own rate."""
    from agent.skills.gate import promotion_rate

    ctx.skills.store("waiting", GOOD_SKILL, {"applies_when": "x"}, {})
    r = promotion_rate(ctx.conn)
    assert r["total"] >= 1 and r["decided"] == 0
    assert r["verdict"] == "no decisions yet"


# ---------------------------------------------------------------------------
# Which gate conditions actually bind, at the scale this suite runs at.
#
# These pin the arithmetic that config.local.yaml's gate section asserts in
# prose. If someone retunes a threshold, the comment and the behaviour must
# not be allowed to drift apart silently.
# ---------------------------------------------------------------------------


def test_min_discordant_pairs_is_the_load_bearing_guard():
    """~75-80% of disagreements must favour the skill, at any n.

    min_paired_trials was never a statistical guard — McNemar reads only
    discordant pairs. Lowering the pool from 30 to 15 while holding
    min_discordant_pairs at 10 RAISES the bar, from 33% of outcomes changed
    to 67%.
    """
    from scipy.stats import binomtest

    expected = {10: 9, 13: 10, 15: 12, 20: 15}
    for discordant, need in expected.items():
        assert binomtest(need, discordant, 0.5, alternative="greater").pvalue < 0.05
        assert binomtest(need - 1, discordant, 0.5,
                         alternative="greater").pvalue >= 0.05, (
            f"{need - 1} of {discordant} should NOT pass")
        assert 0.70 <= need / discordant <= 0.95


def test_min_lift_pp_is_inert_at_this_scale():
    """Do not treat the lift threshold as protection here.

    With min_discordant_pairs=10 and p<0.05 the minimum passing configuration
    is b=9, c=1, so b-c=8 and lift = 800/n. That stays above 5pp for every n
    below 160 — far beyond any suite this project runs. It is retained for
    larger suites, where it starts to bind.
    """
    min_b_minus_c = 8          # b=9, c=1: the smallest significant split
    for n in (15, 30, 100, 160):
        assert 100.0 * min_b_minus_c / n >= 5.0, f"lift would bind at n={n}"
    assert 100.0 * min_b_minus_c / 161 < 5.0, "the crossover should be ~160"


def test_the_shipped_local_gate_config_matches_that_reasoning():
    """The config the calibration will actually run under."""
    import yaml
    from pathlib import Path

    cfg = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config.local.yaml")
        .read_text(encoding="utf-8"))["gate"]
    assert cfg["min_discordant_pairs"] == 10, "the real guard must not be lowered"
    assert cfg["min_paired_trials"] <= 30
    assert cfg["max_p_value"] == 0.05
