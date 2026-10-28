"""An xdist_group must actually keep its tests on one worker.

pyproject's addopts runs `--dist loadgroup`, and the tests that share
something real say so with xdist_group. pytest-xdist groups them by appending
"@<group>" to each marked test's node id. Under pytest 9.2 that suffix is
dropped without an error, so every group spreads across workers: the wheel
build raced itself and the measurement-lock tests refused each other
(2026-10-28, branch cloud-8b). This fails instead of letting that go quiet.
"""

from __future__ import annotations

import pytest


@pytest.mark.xdist_group("group-guard")
def test_an_xdist_group_reaches_the_scheduler(request):
    if not getattr(request.config.option, "loadgroup", False):
        pytest.skip("not a --dist loadgroup worker: there is no group to hold")
    assert request.node.nodeid.endswith("@group-guard"), (
        f"{request.node.nodeid} carries no group: --dist loadgroup is running "
        "as plain --dist load, and no xdist_group in this suite holds. "
        "Check the pytest / pytest-xdist pair in pyproject's dev extras.")
