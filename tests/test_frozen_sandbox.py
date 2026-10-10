from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

import pytest

from agent.execute.sandbox import run_sandboxed
from agent.frozen import CHILD_ENV, SANDBOX_CHILD_FLAG, interpreter_argv, is_frozen

frozen_only = pytest.mark.skipif(not is_frozen(), reason="runs in the frozen probe")


def test_unfrozen_the_sandbox_starts_the_interpreter_it_always_did():
    if is_frozen():
        pytest.skip("the frozen half is below")
    assert interpreter_argv() == [sys.executable, "-I", "-u"]


def test_the_child_runs_isolated_and_is_told_it_is_inside():
    r = run_sandboxed("import os, sys\n"
                      f"print('marker', os.environ.get({CHILD_ENV!r}))\n"
                      "print('isolated-path', any(p in ('', '.') for p in sys.path))\n",
                      timeout_s=60)
    assert r["returncode"] == 0, r
    assert "marker 1" in r["stdout"], r
    assert "isolated-path False" in r["stdout"], "the work directory is importable"


@frozen_only
def test_frozen_the_sandbox_starts_the_binary_as_an_interpreter():
    assert interpreter_argv() == [sys.executable, SANDBOX_CHILD_FLAG]
    r = run_sandboxed("import sys\nprint('frozen child', getattr(sys, 'frozen', False))",
                      timeout_s=60)
    assert r["returncode"] == 0, r
    assert "frozen child True" in r["stdout"], r


@frozen_only
def test_frozen_a_grandchild_of_the_sandbox_is_an_interpreter_not_the_app():
    code = ("import subprocess, sys\n"
            "p = subprocess.run([sys.executable, '-c', 'print(6 * 7)'],\n"
            "                   capture_output=True, text=True, timeout=60)\n"
            "print('GRAND', p.returncode, p.stdout.strip(), p.stderr[-200:])\n")
    r = run_sandboxed(code, timeout_s=90)
    assert "GRAND 0 42" in r["stdout"], r


def _descendants(root: int) -> list[tuple[int, str]]:
    from agent.proc import process_table

    table = process_table()
    kids, out, frontier = {}, [], [root]
    for pid, ppid, image in table:
        kids.setdefault(ppid, []).append((pid, image))
    while frontier:
        for pid, image in kids.get(frontier.pop(), []):
            out.append((pid, image))
            frontier.append(pid)
    return out


@frozen_only
def test_frozen_no_recursive_spawn_by_process_tree():
    exe = os.path.basename(sys.executable).lower()
    before = {p for p, _ in _descendants(os.getpid())}
    code = ("import os, time, glob\n"
            "print('nested', glob.glob(os.path.join(os.getcwd(), 'bucephalus_sbx_*')))\n"
            "time.sleep(4)\nprint('done')\n")
    seen: list[list[tuple[int, str]]] = []

    import threading

    def watch():
        t0 = time.time()
        while time.time() - t0 < 3.5:
            seen.append(_descendants(os.getpid()))
            time.sleep(0.25)

    w = threading.Thread(target=watch)
    w.start()
    r = run_sandboxed(code, timeout_s=60)
    w.join()
    assert "done" in r["stdout"] and "nested []" in r["stdout"], r
    copies = max(len([1 for p, img in snap if img.lower() == exe and p not in before])
                 for snap in seen)
    assert copies == 1, f"{copies} copies of {exe} under the app during one sandbox run"
    time.sleep(1)
    left = [(p, img) for p, img in _descendants(os.getpid())
            if img.lower() == exe and p not in before]
    assert not left, f"left running after the run: {left}"
    leftovers = [d for d in os.listdir(tempfile.gettempdir())
                 if d.startswith("bucephalus_sbx_")
                 and os.path.isdir(os.path.join(tempfile.gettempdir(), d, ))
                 and any(x.startswith("bucephalus_sbx_") for x in
                         os.listdir(os.path.join(tempfile.gettempdir(), d)))]
    assert not leftovers, f"nested sandbox directories: {leftovers}"


@frozen_only
def test_frozen_the_timeout_kills_the_job_including_grandchildren():
    code = ("import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "time.sleep(120)\n")
    exe = os.path.basename(sys.executable).lower()
    before = {p for p, _ in _descendants(os.getpid())}
    t0 = time.time()
    r = run_sandboxed(code, timeout_s=5)
    assert r["stderr"] == "TIMEOUT" and time.time() - t0 < 30, r
    time.sleep(1.5)
    left = [(p, img) for p, img in _descendants(os.getpid())
            if img.lower() == exe and p not in before]
    assert not left, f"the timeout left {left} running"


@frozen_only
def test_frozen_the_binary_outside_any_sandbox_with_the_marker_is_an_interpreter():
    env = {**os.environ, CHILD_ENV: "1"}
    p = subprocess.run([sys.executable, "-c", "print('interp')"], env=env,
                       stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=60)
    assert p.stdout.strip() == "interp", (p.returncode, p.stdout, p.stderr)
