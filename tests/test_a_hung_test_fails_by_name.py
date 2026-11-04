from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))


def test_a_test_that_hangs_is_failed_by_name_instead_of_using_up_the_ci_job():
    assert any(d.startswith("pytest-timeout") for d in PYPROJECT["project"]["optional-dependencies"]["dev"])
    assert 0 < PYPROJECT["tool"]["pytest"]["ini_options"]["timeout"] <= 300
