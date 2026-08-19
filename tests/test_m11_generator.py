"""M11 acceptance — the generator actually runs, and cannot promote.

The previous coverage grepped the module source for the string "promote(",
which proves nothing about behaviour. These call the function.
"""

from __future__ import annotations

import pytest

from agent.assure.mesh import GamingSuspicion
from agent.skills.generate import Source, TaskPattern, generate_skill, promotion_rate

DRAFT = (
    "# csv tidying\n"
    "when cleaning a delimited export\n\n"
    "## procedure\n"
    "Strip the header rows above the real header. Drop fully empty columns.\n"
)

CHECKS = "\n".join(f"CHECK: property {i} holds" for i in range(10))


def _sources() -> list[Source]:
    return [Source(url="file://notes.md", content="csv exports carry junk header rows",
                   retrieved="2026-08-19")]


def _wire(ctx, draft: str = DRAFT) -> None:
    """Drafter is the local tier; test author is cloud. Different backends."""
    ctx.config.active_local_tier = "local"
    ctx.backends["local"].queue(draft)
    ctx.backends["cloud"].queue(CHECKS)


def test_generate_skill_stores_a_candidate(ctx):
    _wire(ctx)
    sid = generate_skill(ctx, TaskPattern(description="tidy a csv", name="csv-tidy"),
                         sources=_sources())

    rec = ctx.skills.load(sid, verify=False)
    assert rec["status"] == "candidate", "the generator must never ship a live skill"
    assert rec["provenance"]["drafted_by"] == "local"
    assert rec["provenance"]["tests_authored_by"] == "cloud"
    assert rec["provenance"]["sources"][0]["url"] == "file://notes.md"
    assert sid in ctx.gate_queue(), "the candidate must be handed to the gate"


def test_generated_holdout_is_registered_and_partitioned(ctx):
    _wire(ctx)
    sid = generate_skill(ctx, TaskPattern(description="tidy a csv", name="csv-tidy"),
                         sources=_sources())

    holdout = ctx.mesh.holdouts.get(sid)
    visible = ctx.skills.visible_tests(sid)
    assert holdout, "held-out checks were never registered"
    assert not set(holdout) & set(visible), "a check is in both partitions"
    # 30% of 10 checks
    assert len(holdout) == 3 and len(visible) == 7


def test_generator_refuses_a_comprehensive_skill(ctx):
    """Spec M9: >3 modules measurably hurt, so it never reaches the store."""
    fat = "# big\n" + "".join(f"## module {i}\ndo a thing\n" for i in range(5))
    _wire(ctx, draft=fat)
    with pytest.raises(ValueError, match="module limit"):
        generate_skill(ctx, TaskPattern(description="everything", name="kitchen-sink"),
                       sources=_sources())


def test_author_falls_back_to_a_different_backend_with_no_local_tier(ctx):
    """M7.4 rule 1 holds even when the drafter is the cloud tier.

    With no local tier the drafter is `cloud`, so the test author must move to
    `review` rather than collide. This is the branch that keeps the anti-gaming
    invariant true in the all-cloud configuration.
    """
    ctx.config.active_local_tier = None
    ctx.backends["cloud"].queue(DRAFT)
    ctx.backends["review"].queue(CHECKS)

    sid = generate_skill(ctx, TaskPattern(description="tidy a csv", name="csv-tidy"),
                         sources=_sources())
    prov = ctx.skills.load(sid, verify=False)["provenance"]
    assert prov["drafted_by"] == "cloud"
    assert prov["tests_authored_by"] == "review"
    assert prov["drafted_by"] != prov["tests_authored_by"]


def test_same_backend_for_both_roles_is_refused():
    """The guard itself, called directly."""
    from agent.assure.mesh import assert_different_authors

    with pytest.raises(GamingSuspicion):
        assert_different_authors("cloud", "cloud")


def test_generation_without_grounding_refuses(ctx):
    """No sources and no grounding/ directory: refuse rather than use priors."""
    ctx.config.active_local_tier = "local"
    with pytest.raises(RuntimeError, match="grounding"):
        generate_skill(ctx, TaskPattern(description="something ungrounded",
                                        name="ungrounded"))


def test_promotion_rate_reports_candidates(ctx):
    _wire(ctx)
    generate_skill(ctx, TaskPattern(description="tidy a csv", name="csv-tidy"),
                   sources=_sources())
    r = promotion_rate(ctx.conn)
    assert r["total"] == 1 and r["promoted"] == 0
    assert r["verdict"] == "no decisions yet"
