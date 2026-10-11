from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_DIRS = ("bench", "packaging", "scripts")
ENTRY_POINTS = sorted(p for d in TOOL_DIRS for p in (ROOT / d).rglob("*.py") if "__pycache__" not in p.parts)
SCRATCH = ("BUCEPHALUS_DATA_DIR", "BUCEPHALUS_CONFIG_DIR", "BUCEPHALUS_SCRATCH_ONLY")

BOOT = """
import sys, types
entry = sys.argv[1]
sys.argv[0] = entry
sys.modules["__main__"].__file__ = entry
from agent import paths
assert hasattr(paths, "RealDirRefused")
for fn in (paths.user_data_dir, paths.user_config_dir):
    try:
        print("RESOLVED", fn())
    except paths.RealDirRefused as e:
        print("REFUSED", e)
"""


def _require_guard():
    from agent import paths

    assert hasattr(paths, "RealDirRefused"), "no guard: not spawning anything that could resolve the real directories"


def _env(**extra):
    _require_guard()
    env = {k: v for k, v in os.environ.items() if k not in SCRATCH}
    env.update(PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1", **extra)
    return env


def _boot(entry: Path, env) -> str:
    r = subprocess.run([sys.executable, "-c", BOOT, str(entry)], cwd=str(ROOT), env=env,  # noqa: S603
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return r.stdout


def test_there_are_entry_points_to_walk():
    names = {p.relative_to(ROOT).as_posix() for p in ENTRY_POINTS}
    assert {"bench/run.py", "bench/profile_run.py", "packaging/build.py", "scripts/postinstall_check.py"} <= names


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_tool_entry_point_refuses_real_user_dirs(entry):
    out = _boot(entry, _env())
    assert out.count("REFUSED") == 2 and "RESOLVED" not in out, out


@pytest.mark.parametrize("entry", ENTRY_POINTS[:3], ids=lambda p: p.relative_to(ROOT).as_posix())
def test_an_explicit_scratch_dir_is_used(entry, tmp_path):
    out = _boot(entry, _env(BUCEPHALUS_DATA_DIR=str(tmp_path / "d"), BUCEPHALUS_CONFIG_DIR=str(tmp_path / "c")))
    assert "REFUSED" not in out and str(tmp_path / "d") in out and str(tmp_path / "c") in out


def test_a_child_of_a_tool_process_is_refused_too():
    child = ("import subprocess, sys, os\n"
             "env = {k: v for k, v in os.environ.items() if k not in ('BUCEPHALUS_DATA_DIR', 'BUCEPHALUS_CONFIG_DIR')}\n"
             "r = subprocess.run([sys.executable, '-c', 'from agent import paths\\n"
             "try:\\n    paths.user_data_dir(); print(\"RESOLVED\")\\nexcept paths.RealDirRefused: print(\"REFUSED\")'],"
             " env=env, capture_output=True, text=True)\n"
             "print('CHILD', r.stdout.strip(), r.stderr[-300:])\n")
    boot = BOOT + "\nexec(" + repr(child) + ")\n"
    r = subprocess.run([sys.executable, "-c", boot, str(ROOT / "bench" / "run.py")], cwd=str(ROOT), env=_env(),  # noqa: S603
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert "CHILD REFUSED" in r.stdout, r.stdout + r.stderr[-800:]


def test_an_ordinary_process_is_not_affected(tmp_path):
    r = subprocess.run([sys.executable, "-c", "from agent import paths; print(paths.scratch_only())"],  # noqa: S603
                       cwd=str(tmp_path), env=_env(), capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "False", r.stderr[-800:]


def test_a_real_script_stops_before_touching_anything(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "postinstall_check.py")], cwd=str(tmp_path),  # noqa: S603
                       env=_env(), capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode != 0 and "RealDirRefused" in r.stderr, (r.stdout + r.stderr)[-1500:]
