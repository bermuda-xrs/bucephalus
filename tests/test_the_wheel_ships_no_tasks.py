from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.xdist_group("wheel-build")


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> zipfile.ZipFile:
    out = tmp_path_factory.mktemp("wheel")
    r = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps",
         "-w", str(out), "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    built = list(out.glob("*.whl"))
    offline = any(sig in r.stderr for sig in ("NameResolutionError", "No matching distribution",
                                              "Temporary failure in name resolution"))
    if r.returncode != 0 and offline:
        pytest.skip("no network for the wheel build's isolated environment")
    assert r.returncode == 0 and built, (
        f"could not build a wheel (exit {r.returncode}):\n"
        f"{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return zipfile.ZipFile(built[0])


def test_the_wheel_contains_no_bench_directory(wheel):
    inside = [n for n in wheel.namelist()
              if n.startswith("bench/") or "/bench/" in n]
    assert not inside, f"the benchmark shipped: {inside[:10]}"


def test_the_wheel_contains_no_task_files(wheel):
    data = [n for n in wheel.namelist()
            if n.endswith((".json", ".yaml", ".yml"))
            and not n.startswith("alpha_test_1-")]
    assert not data, f"task-shaped data in the wheel: {data}"


MAY_NAME_A_GRADING_KEY = {"agent/control/ui.html", "agent/control/ui.js"}


def test_no_grading_key_is_anywhere_in_the_wheel(wheel):
    found = []
    for name in wheel.namelist():
        if name.endswith("/") or name.endswith(".py"):
            continue
        if name in MAY_NAME_A_GRADING_KEY:
            continue
        body = wheel.read(name)
        for key in (b"x-expected", b"x-contains"):
            if key in body:
                found.append(f"{name}: {key.decode()}")
    assert not found, f"grading keys shipped as data: {found}"


def test_the_wheel_ships_only_the_package(wheel):
    tops = {n.split("/")[0] for n in wheel.namelist()}
    assert tops == {"agent", "alpha_test_1-0.1.0.dist-info"}, sorted(tops)


def test_the_only_non_python_payload_is_the_runtime_assets(wheel):
    payload = sorted(n for n in wheel.namelist()
                     if not n.endswith(".py")
                     and not n.startswith("alpha_test_1-")
                     and not n.endswith("/"))
    assert payload == ["agent/assets/banner.ans", "agent/control/md.js", "agent/control/ui.css",
                       "agent/control/ui.html", "agent/control/ui.js",
                       "agent/themes/default.toml"], payload
