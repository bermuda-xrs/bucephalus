"""Audit probes: can scripts/mutate.py report a wrong result?

`_run` is replaced by a fake, so no mutant is ever run and nothing but a temp
copy of src/ is written. The module named is real (it must lie under the
checkout), and only its private copies are touched.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("mutate_probe", ROOT / "scripts" / "mutate.py")
mutate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mutate)

MODULE = str(ROOT / "src" / "agent" / "security" / "origin.py")


def _fake(results_for_whole_suite: str):
    calls = []

    def _run(tree_dir, tests, timeout):
        calls.append(tests)
        if tests[0] == "tests":                       # the --recheck path
            is_baseline = len([c for c in calls if c[0] == "tests"]) == 1
            return ("survived" if is_baseline else results_for_whole_suite), 1.0
        return "survived", 1.0                        # targeted tests: every mutant survives
    return _run, calls


def test_FINDING_a_timeout_in_the_recheck_is_counted_as_a_kill(tmp_path, monkeypatch):
    """The fix for the first run's instrument bug made a TIMEOUT "undecided"
    in the targeted phase. The recheck phase still reads anything that is not
    "survived" as `killed-by-suite` - a timeout included (mutate.py:258-260) -
    so the reported score rises with contention again."""
    fake, _ = _fake("timeout")
    monkeypatch.setattr(mutate, "_run", fake)
    out = tmp_path / "r.json"
    mutate.main([MODULE, "tests/test_origin.py", "--only", "is_channel", "--jobs", "1",
                 "--recheck", "--out", str(out)])
    res = json.loads(out.read_text(encoding="utf-8"))
    print({r["op"]: r["status"] for r in res["results"]}, "score", res["score"])
    assert res["results"] and all(r["status"] == "killed-by-suite" for r in res["results"])
    assert res["score"] == 1.0          # every mutant "killed", none actually was


def test_FINDING_any_failure_anywhere_in_the_whole_suite_is_a_kill(tmp_path, monkeypatch):
    """-x on the whole suite: the first unrelated failure (a flaky test, a
    busy real measurement lock, a port in use) marks the survivor killed.
    The output goes to DEVNULL, so which test "killed" it is not recorded."""
    fake, calls = _fake("killed")
    monkeypatch.setattr(mutate, "_run", fake)
    out = tmp_path / "r.json"
    mutate.main([MODULE, "tests/test_origin.py", "--only", "is_channel", "--jobs", "1",
                 "--recheck", "--out", str(out)])
    res = json.loads(out.read_text(encoding="utf-8"))
    assert all(r["status"] == "killed-by-suite" for r in res["results"])
    assert ["tests", "-n", "12", "--dist", "loadgroup"] in calls
    src =(ROOT / "scripts" / "mutate.py").read_text(encoding="utf-8")
    assert '"-x"' in src and "stdout=subprocess.DEVNULL" in src
