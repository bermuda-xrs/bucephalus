from __future__ import annotations

import os
import shutil
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
    "bench/g1_close.py",
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
    r = _run([sys.executable, "-m", "agent.cli", "--help"], timeout=180)
    assert r.returncode == 0, r.stderr[-500:]
    assert "usage" in r.stdout.lower()


def test_the_demo_runs_end_to_end():
    r = _run([sys.executable, str(ROOT / "demo.py")], timeout=600)
    assert r.returncode == 0, f"demo.py failed:\n{r.stderr[-600:]}"
    out = r.stdout.replace("\n", "")
    assert "chain verifies: True" in out, out[-300:]
    assert "model calls" in out, "the demo stopped before its summary"


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"), reason="Windows PowerShell")
def test_the_powershell_launcher_passes_the_exit_code_on():
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "bucephalus.ps1"),
                        "no-such-command"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, (r.returncode, r.stderr[-400:])
