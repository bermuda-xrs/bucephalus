"""A2-3 - the generation gate holds in code, not only in FINDINGS 1.21.

M7.4 rules 2 (the visible/held-out gap) and 3 (isomorphic verification) have
no production caller, and they exist to police what `generate_skill` produces.
The first real generation must not precede them, so `generate_skill` refuses
until whoever wires them flips the flag. Tests of the generator's OWN rules
opt out with the `generator_unguarded` fixture, visibly.
"""

from __future__ import annotations

import pytest

import agent.skills.generate as gen
from agent.skills.generate import TaskPattern, generate_skill


def test_generation_is_refused_until_m74_rules_2_and_3_run(ctx):
    calls: list = []
    for b in ctx.backends.values():
        b._responder = lambda p, t: calls.append(p) or "# x\n## m\ndo it\n"  # noqa: SLF001
    with pytest.raises(getattr(gen, "GenerationGated", type(None)), match="FINDINGS 1.21"):
        generate_skill(ctx, TaskPattern(description="tidy a csv", name="csv-tidy"),
                       sources=[])
    assert calls == [], "a model was called before the gate refused"
