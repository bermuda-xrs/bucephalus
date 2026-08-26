"""Machinery with green tests and zero call sites. It grew back.

An audit early in this project found seven pieces of spec machinery that were
unit-tested and never called by the running agent. `test_wired_invariants.py`
exists because of it. A fresh audit on 2026-08-27 found nine more, so the
pattern is not a one-off — it is what happens by default when a milestone is
implemented, tested, and then not plugged in.

This test freezes the known set. A NEW unreferenced function fails it. The
existing ones are listed with why they are tolerated, so the list is a record
rather than a suppression.

The two that actually matter are called out in `KNOWN_INERT`:

  * `record_holdout_gap` is never called, so `_holdout_gap` always returns
    None and the gate's anti-gaming veto (M7.4 rule 4) CANNOT FIRE in a real
    run. That veto is the one guarding against a skill fitted to the checks
    it can see — precisely the failure expected from generated skills, which
    is the next phase of the project.
  * `local_with_escalation` is never called, so escalation-by-disagreement
    (M6.3) does not happen; the router escalates on allowlist rules alone.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agent"

# name -> why it is unreferenced and whether that is acceptable
KNOWN_INERT = {
    # --- inert guarantees. These are the expensive ones. -------------------
    "record_holdout_gap":
        "M7.4 rule 4 is INERT: nothing records the visible/held-out gap, so "
        "the gate's anti-gaming veto can never fire. Must be wired before "
        "any generated skill is evaluated.",
    "local_with_escalation":
        "M6.3 escalation-by-disagreement never runs; the router escalates on "
        "allowlist rules only.",
    "measure_retrieval_precision":
        "M9.4 measures retrieval separately from quality. Never invoked, so "
        "'the right skill existed and was not retrieved' is unmeasured.",

    # --- superseded by an inline implementation ---------------------------
    "refine_with_ratchet":
        "The ratchet is implemented inline in execute/loop.py (best_score). "
        "This is a second, unused implementation of the same rule.",
    "quarantined_summarize":
        "loop.condense_observation inlines the quarantine via "
        "QUARANTINE_PREFIX and tools=None. Second implementation.",
    "choose_strategy":
        "assure/mesh picks verifiers directly.",
    "choose_action_mode":
        "execute/loop decides code-vs-action inline.",

    # --- not yet reached in the build order -------------------------------
    "generate_skill":
        "M11. No generated skill has been produced yet - by design, the gate "
        "was built first.",
    "import_external_skill":
        "External skills go through the same gate; none imported yet.",

    # --- small helpers, genuinely unused ----------------------------------
    "artifacts_for": "helper; superseded by ledger/artifacts.project().",
    "children": "task-tree helper; no caller.",
    "contract_hash": "helper; plan pinning hashes the plan, not the contract.",
    "has_receipt": "receipts are checked inside execute_once.",
    "deviation_rate": "reporting helper for M13.4; never surfaced.",
    "delete_step": "plan editing is done through the console API.",
    "edit_step": "plan editing is done through the console API.",
}


def _unreferenced() -> dict[str, Path]:
    defs = {}
    for f in SRC.rglob("*.py"):
        for node in ast.parse(f.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_") or node.name == "main":
                    continue
                if node.decorator_list:          # framework-registered
                    continue
                defs[node.name] = (f, node.lineno)

    prod = list(SRC.rglob("*.py")) + list((ROOT / "bench").glob("*.py"))
    prod += [ROOT / "demo.py"]
    bodies = {f: f.read_text(encoding="utf-8").splitlines()
              for f in prod if f.exists()}

    out = {}
    for name, (home, lineno) in defs.items():
        for f, lines in bodies.items():
            for i, line in enumerate(lines, 1):
                if f == home and i == lineno:
                    continue                     # the def itself
                if re.search(rf"\b{re.escape(name)}\b", line):
                    break
            else:
                continue
            break
        else:
            out[name] = home
    return out


def test_no_newly_unreferenced_machinery():
    """A new function with no call site is the failure this project repeats."""
    found = _unreferenced()
    new = sorted(set(found) - set(KNOWN_INERT))
    assert not new, (
        "these have no call site in production code:\n  "
        + "\n  ".join(f"{n}  ({found[n].relative_to(ROOT)})" for n in new)
        + "\n\nWire it, delete it, or add it to KNOWN_INERT with the reason. "
          "A test that a function works is not a test that the system calls it."
    )


def test_the_known_list_does_not_rot():
    """An entry that got wired should leave the list, or it stops meaning
    anything."""
    found = _unreferenced()
    stale = sorted(set(KNOWN_INERT) - set(found))
    assert not stale, (
        f"these are now referenced and should leave KNOWN_INERT: {stale}")


def test_the_inert_guarantees_are_named_as_such():
    """The point of the list is that the expensive ones stay visible."""
    for name in ("record_holdout_gap", "local_with_escalation"):
        assert name in KNOWN_INERT
        assert "INERT" in KNOWN_INERT[name] or "never" in KNOWN_INERT[name]
