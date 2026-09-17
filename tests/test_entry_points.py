"""Every documented entry point, actually invoked.

2026-09-16. Two front doors were dead for ten days, both broken by ONE commit
(b0bdcd7, 2026-09-06), neither noticed:

  * `bench/noise.py main` - step 1 of the README's measurement flow - read
    `ctx` forty-five lines before it was assigned, so every run died with
    UnboundLocalError before its first task (SPEC-DELTA C64);
  * `demo.py` - the README's and HANDOVER's end-to-end proof - formatted an
    unmeasured cache ratio with `:.0%` and died on its last line, after doing
    all the work (C58's defect, in the second place nobody swept).

769 tests passed around both, because a test imports what an entry point
CALLS rather than calling the entry point. FINDINGS 1.28. These call them.

`--help` is the cheapest real invocation: it runs the module's imports and
BUILDS THE PARSER, which is where a missing import or a mis-wired subcommand
shows up. It is not a claim that the command works - `test_noise_main.py`
drives one to its first decision, and `demo.py` is run in full below.

`bench/cache_reuse_probe.py` is deliberately absent: it has no parser and
needs a live model server, so there is nothing here that can invoke it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1",
       "PYTHONDONTWRITEBYTECODE": "1"}

ENTRY_POINTS = [
    "bench/noise.py",
    "bench/run.py",
    "bench/report.py",
    "bench/coverage.py",
    "bench/ladder.py",
    "bench/nextstep.py",
    "bench/inventory.py",
    "bench/g1_ablation_run.py",
    "bench/cache_boundary.py",
    "bench/suites/archive/aero_calc.py",
    "bench/archive/decompose_aero.py",
]


def _run(argv, timeout=300):
    return subprocess.run(argv, cwd=str(ROOT), env=ENV, capture_output=True,  # noqa: S603
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


@pytest.mark.parametrize("script", ENTRY_POINTS)
def test_the_entry_point_starts(script):
    r = _run([sys.executable, str(ROOT / script), "--help"], timeout=180)
    assert r.returncode == 0, f"{script} --help failed:\n{r.stderr[-500:]}"
    assert "usage" in (r.stdout + r.stderr).lower(), f"{script}: no usage line"


def test_the_cli_starts():
    """The `alpha` console script, through the same module it installs."""
    r = _run([sys.executable, "-m", "agent.cli", "--help"], timeout=180)
    assert r.returncode == 0, r.stderr[-500:]
    assert "usage" in r.stdout.lower()


def test_the_demo_runs_end_to_end():
    """README and HANDOVER both point a newcomer at this, and it is RELEASE's
    one end-to-end demonstration. Scripted model, no network. It printed its
    whole story and then died on the last line."""
    r = _run([sys.executable, str(ROOT / "demo.py")], timeout=600)
    assert r.returncode == 0, f"demo.py failed:\n{r.stderr[-600:]}"
    out = r.stdout.replace("\n", "")
    assert "chain verifies: True" in out, out[-300:]
    assert "model calls" in out, "the demo stopped before its summary"
