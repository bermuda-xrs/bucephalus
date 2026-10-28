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
    src = NOISE.read_text(encoding="utf-8")
    for const in ("COMMIT_RATIO_SUSPECT", "EMERGENCY_FREE_GB",
                  "REFRESH_COMMIT_RATIO"):
        assert f"{const} = " in src, f"{const} is not defined in noise.py"


def test_refresh_triggers_below_the_suspect_threshold():
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
    assert _calls("memory_pressure") >= 3, (
        "expected memory_pressure() at preflight, per-task, and the refresh "
        "trigger; a call site that computes its own answer is a second copy "
        "of the rule")


def test_pressure_and_rss_are_readable_here():
    p = memory_pressure()
    assert p, "memory_pressure() returned empty on this platform"
    for k in ("available_gb", "committed_gb", "physical_gb", "commit_ratio"):
        assert k in p, f"memory_pressure() is missing {k}"
    assert p["available_gb"] > 0 and p["physical_gb"] > 0
    if sys.platform == "darwin":
        # macOS has no commit charge to read: unmeasured, so null, never zero
        # (invariant 36; docs/PLATFORMS.md, memory metrics).
        assert p["committed_gb"] is None and p["commit_ratio"] is None, p
    else:
        assert p["commit_ratio"] > 0

    rss = worker_rss_gb()
    assert rss is None or rss > 0


def test_order_seed_is_in_the_resume_signature():
    src = NOISE.read_text(encoding="utf-8")
    start = src.index("def run_signature(")
    end = src.index("def append_run(")
    assert "order_seed" in src[start:end], (
        "order_seed must be part of run_signature, or --resume will blend a "
        "ledger written under one task order with rows measured under another")
