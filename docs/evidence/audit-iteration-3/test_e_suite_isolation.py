"""Audit probes: what the (now parallel) suite shares with the real machine.
Nothing here opens or writes the operator's real files: the first probe only
asks a subprocess WHERE it would look, the second runs a suite test from a
scratch working directory.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable


def test_FINDING_the_demo_subprocess_resolves_the_operators_real_user_dirs():
    """tests/test_entry_points.py builds ENV at IMPORT time from os.environ,
    before conftest's autouse fixture has set ALPHA_CONFIG_DIR, and the
    user-data redirect is a monkeypatch of platformdirs, which a subprocess
    does not inherit. `test_the_demo_runs_end_to_end` runs demo.py with that
    ENV; demo.py builds an AgentContext, whose memory is
    user_data_dir()/memory - the operator's real tier-1 memory, which is then
    snapshotted into the demo's session row under runs/demo-*/agent.db, in the
    synced checkout (38edfa1 and 61227e9 each fixed the in-process half)."""
    spec = importlib.util.spec_from_file_location("_ep", ROOT / "tests" / "test_entry_points.py")
    ep = importlib.util.module_from_spec(spec)
    saved = {k: os.environ.pop(k) for k in ("ALPHA_DATA_DIR", "ALPHA_CONFIG_DIR") if k in os.environ}
    try:                     # the module is imported at collection, before any fixture ran
        spec.loader.exec_module(ep)
    finally:
        os.environ.update(saved)
    assert "ALPHA_DATA_DIR" not in ep.ENV and "ALPHA_CONFIG_DIR" not in ep.ENV
    code = ("import json, platformdirs; from agent.paths import user_data_dir, user_config_dir, APP;"
            "print(json.dumps({'memory': str(user_data_dir() / 'memory'),"
            " 'real': platformdirs.user_data_dir(APP), 'token_dir': str(user_config_dir())}))")
    out = subprocess.run([PY, "-c", code], env=ep.ENV, cwd=str(ROOT), capture_output=True,
                         text=True, timeout=120)
    got = json.loads(out.stdout)
    print(got)
    assert got["memory"].startswith(got["real"])        # a path only; nothing is opened here
    demo = (ROOT / "demo.py").read_text(encoding="utf-8")
    assert 'HERE / "runs" / "demo"' in demo and "ALPHA_DATA_DIR" not in demo


def test_FINDING_an_archived_bench_test_writes_cal_json_into_the_working_directory(tmp_path):
    """D15 was fixed in ONE of the two tests that build `_FakeCtx(profile)`
    without tmp_path. `test_paired_split_filter_keeps_the_contamination_boundary`
    still resolves --out against Path("."): run from the checkout, as the suite
    is, it rewrites the repository's cal.json - which the gate lists as project
    state - on every run (observed: cal.json mtime = the audit's suite run).
    Here it is run from a scratch working directory instead."""
    env = dict(os.environ, PYTHONUTF8="1", ALPHA_DATA_DIR=str(tmp_path / "d"),
               ALPHA_CONFIG_DIR=str(tmp_path / "c"))
    target = (ROOT / "tests" / "archive" / "test_bench_suite.py").as_posix() + \
        "::test_paired_split_filter_keeps_the_contamination_boundary"
    r = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0",
                        "--rootdir", str(ROOT), "-c", str(ROOT / "pyproject.toml"), target],
                       cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=300)
    print(r.stdout[-300:])
    assert r.returncode == 0
    assert (tmp_path / "cal.json").exists(), sorted(p.name for p in tmp_path.iterdir())
