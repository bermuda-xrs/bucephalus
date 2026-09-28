"""The product ships empty. No task content reaches the installed package.

`bench/` is this project's OWN benchmark - the instrument it uses to measure
G1 and the skill gate - and not a suite bundled for users. `--suite` is a
required argument with no default precisely so that a user's numbers are
always about a suite they wrote; see `bench/README.md`.

Two reasons this is a test and not a convention:

  * a shipped task is a shipped ANSWER. Every `expected_value` task carries
    `x-expected` in its file. Putting those in site-packages hands the
    grading key to anything that reads the wheel, which is the same defect
    class as the 2026-09-22 prompt leak by yet another route.
  * the exclusion is currently STRUCTURAL - `bench/` sits outside `src/`, and
    `[tool.setuptools.packages.find] where = ["src"]` cannot see it. Nothing
    declares the intent, so one `package-data` glob or one `MANIFEST.in`
    would undo it silently. Structure is not a decision until something
    checks it.

This builds a real wheel (~10 s) rather than reading `pyproject.toml`.
Reading the config tests the config; the question is what is in the artifact.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> zipfile.ZipFile:
    """A freshly built wheel.

    `pip wheel`, not `python -m build`: the repository has its own `build/`
    directory, which shadows the `build` module and fails with "No module
    named build.__main__" - a trap documented in HANDOVER B1.
    """
    out = tmp_path_factory.mktemp("wheel")
    r = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps",
         "-w", str(out), "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    built = list(out.glob("*.whl"))
    assert r.returncode == 0 and built, (
        f"could not build a wheel (exit {r.returncode}):\n"
        f"{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return zipfile.ZipFile(built[0])


def test_the_wheel_contains_no_bench_directory(wheel):
    inside = [n for n in wheel.namelist()
              if n.startswith("bench/") or "/bench/" in n]
    assert not inside, f"the benchmark shipped: {inside[:10]}"


def test_the_wheel_contains_no_task_files(wheel):
    """A task file is an answer key. None may ship, under any name."""
    data = [n for n in wheel.namelist()
            if n.endswith((".json", ".yaml", ".yml"))
            and not n.startswith("alpha_test_1-")]
    assert not data, f"task-shaped data in the wheel: {data}"


# Files that may legitimately NAME a grading key, each reviewed once.
#
# Source names the keys because it reads them: `assure/verifiers.py` grades
# with them, and the console's `ui.html` lets a human author and inspect a
# task, so it writes `schema["x-expected"]` from a form field. Naming a key is
# not shipping an answer.
#
# Reviewed by PATH, not by extension, so a task file called `suite.txt` is
# still caught. A newly shipped asset is caught by
# `test_the_only_non_python_payload_is_the_runtime_assets` first, which is
# what forces the review that would add it here.
# The console script moved out of ui.html for the CSP (iteration 3, 1.5).
MAY_NAME_A_GRADING_KEY = {"agent/control/ui.html", "agent/control/ui.js"}


def test_no_grading_key_is_anywhere_in_the_wheel(wheel):
    """Belt and braces, independent of where a task file sits or what it is
    called: grep the whole archive for the keys a grader reads."""
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
    """Nothing but `agent/` and its metadata. A new top-level directory in
    the wheel is a packaging change somebody should have to justify."""
    tops = {n.split("/")[0] for n in wheel.namelist()}
    assert tops == {"agent", "alpha_test_1-0.1.0.dist-info"}, sorted(tops)


def test_the_only_non_python_payload_is_the_runtime_assets(wheel):
    """An installed wheel has no project directory, so it carries its theme,
    banner and console HTML. That is the whole list, and it stays the whole
    list."""
    payload = sorted(n for n in wheel.namelist()
                     if not n.endswith(".py")
                     and not n.startswith("alpha_test_1-")
                     and not n.endswith("/"))
    assert payload == ["agent/assets/banner.ans", "agent/control/ui.css",
                       "agent/control/ui.html", "agent/control/ui.js",
                       "agent/themes/default.toml"], payload
