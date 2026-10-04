"""One memory rule, expressed once.

The 2 GB free-memory floor is a PREFLIGHT figure - taken with no model
loaded. It was copied into a per-task check that runs with the model
resident, where a healthy run sits at 1.1-1.5 GB available, so it flagged
100% of a correct run and the abort rule built on it would have killed a
healthy pass within six tasks.

That was corrected per-task. The preflight kept its own copy of the same
wrong comparison and went on warning spuriously - the bug survived in the
one call site that was not touched. Two copies of a rule is how that
happens, so these tests fail if a third appears.

They also pin the ordering the design depends on: refresh must trigger
BELOW suspect, or the run only repairs itself after a task has already been
flagged, and the flag stops meaning "this task was measured under pressure".
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOISE = ROOT / "bench" / "noise.py"

sys.path.insert(0, str(ROOT / "src"))

from agent.proc import memory_pressure, worker_rss_gb  # noqa: E402


def _tree():
    return ast.parse(NOISE.read_text(encoding="utf-8"))


def _calls(name: str) -> int:
    return sum(
        1 for n in ast.walk(_tree())
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == name)


def test_thresholds_are_module_constants_not_literals():
    """The rule lives in named constants, so there is somewhere to change it."""
    src = NOISE.read_text(encoding="utf-8")
    for const in ("COMMIT_RATIO_SUSPECT", "EMERGENCY_FREE_GB",
                  "REFRESH_COMMIT_RATIO"):
        assert f"{const} = " in src, f"{const} is not defined in noise.py"


def test_refresh_triggers_below_the_suspect_threshold():
    """Repair must happen before flagging, or the flag loses its meaning."""
    ns: dict = {}
    for node in _tree().body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    ns[t.id] = node.value.value
    assert ns["REFRESH_COMMIT_RATIO"] < ns["COMMIT_RATIO_SUSPECT"], (
        "the worker refresh must fire BEFORE a task is flagged suspect; "
        f"refresh={ns['REFRESH_COMMIT_RATIO']} "
        f"suspect={ns['COMMIT_RATIO_SUSPECT']}")


def test_no_bare_two_gigabyte_free_memory_comparison_survives():
    """The exact defect: a no-model-loaded figure compared against a
    model-resident reading. It was fixed per-task and left in preflight."""
    src = NOISE.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Compare):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if "free_gb" in seg or "free_memory_gb" in seg:
            for c in node.comparators:
                if isinstance(c, ast.Constant) and c.value == 2.0:
                    raise AssertionError(
                        "a free-memory-vs-2.0 comparison is back in "
                        f"noise.py: {seg!r}. 2 GB is a PREFLIGHT figure and "
                        "does not apply once the model is resident.")


def test_both_gates_read_the_same_function():
    """Preflight and per-task must both go through memory_pressure()."""
    assert _calls("memory_pressure") >= 3, (
        "expected memory_pressure() at preflight, per-task, and the refresh "
        "trigger; a call site that computes its own answer is a second copy "
        "of the rule")


def test_pressure_and_rss_are_readable_here():
    """A guard that silently returns nothing guards nothing."""
    p = memory_pressure()
    assert p, "memory_pressure() returned empty on this platform"
    for k in ("available_gb", "committed_gb", "physical_gb", "commit_ratio"):
        assert k in p, f"memory_pressure() is missing {k}"
    assert p["commit_ratio"] > 0

    # worker_rss_gb legitimately returns None when no model is loaded; what
    # must not happen is an exception or a nonsense value.
    rss = worker_rss_gb()
    assert rss is None or rss > 0


def test_order_seed_is_in_the_resume_signature():
    """Two task orders in one ledger are two sets of conditions, blended."""
    src = NOISE.read_text(encoding="utf-8")
    start = src.index("def run_signature(")
    end = src.index("def append_run(")
    assert "order_seed" in src[start:end], (
        "order_seed must be part of run_signature, or --resume will blend a "
        "ledger written under one task order with rows measured under another")
