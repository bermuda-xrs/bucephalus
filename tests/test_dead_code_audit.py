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
    "resume":
        "M1.4/M1.5 THE RESUME PROTOCOL IS INERT. `integrity.resume()` verifies "
        "the chain and halts on a break BEFORE a task continues, and nothing "
        "imports it - `cli.py`, `server.py`, `loop.py` and `planner.py` each "
        "import other names from that module and never this one. So resuming "
        "a paused task does not verify its chain. Surfaced 2026-09-07 when "
        "the audit stopped crediting method calls as module-function "
        "references; the old rule counted `--resume` and prose mentions. "
        "DEFERRED D20.",
    "freeze_skills":
        "A second implementation. `AgentContext.freeze_skills` is the one the "
        "loop calls; this module-level version in ledger/prefix.py has no "
        "caller. It looked wired only because the audit credited the METHOD "
        "call `ctx.freeze_skills(task)` to it.",
    "calibrate":
        "CORRECTIONS 1.2 replaced tiktoken with a local estimator for in-loop "
        "budgeting PLUS the provider's count_tokens for calibration. The "
        "calibration half has no caller, so _CHARS_PER_TOKEN is never "
        "measured against a real tokenizer and EVERY token figure in this "
        "project is an uncalibrated estimate. It also cannot run under "
        "--profile local: count_tokens_exact needs a provider client and "
        "there is no API key. Surfaced 2026-09-03 when the audit stopped "
        "counting comment mentions as call sites.",

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
    "reset":
        "test-only hook clearing net/politeness.py's robots and rate-limit "
        "caches, which are module-level and would otherwise leak between "
        "tests. No production call site BY DESIGN: a reset reachable from a "
        "real run is a way to bypass a site's crawl-delay.",
    "reset_injection_counter":
        "test-only helper for the ALPHA_INJECT_STALL fault-injection switch. "
        "Production has no call site BY DESIGN - a reset reachable from a "
        "real run is one more way for injection to affect a measurement.",
    "describe":
        "profile banner helper; report headers build their own provenance "
        "block from profile_provenance instead. Surfaced 2026-09-03 by the "
        "same audit fix as `calibrate`.",
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

    # References are collected from the AST, not by grepping lines.
    #
    # The regex version counted any line CONTAINING the name, so a mention in
    # a comment or a docstring registered as a call site. It was caught when a
    # docstring reading "does NOT kill live children of a running server"
    # made the unused `children` helper look wired.
    #
    # That is this project's own instrument-bug pattern, in the test written
    # to prevent it: the check measured "the token appears in a line", not
    # "the system calls it". Ask what it scores for code that did the work
    # perfectly but behaved slightly unusually - here, code that merely
    # NAMES a function in prose - and the answer was "wired".
    refs: set[str] = set()
    for f in prod:
        if not f.exists():
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))

        # Names bound by an import in THIS file. Used to decide whether an
        # attribute access is a module-qualified call or a method call.
        module_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    module_names.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    module_names.add(a.asname or a.name)

        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                refs.add(node.id)
            elif isinstance(node, ast.Attribute):
                # RECEIVER-AWARE, and this is the second bug fixed in this
                # function rather than the first.
                #
                # Crediting every `X.name` as a reference to a module-level
                # `name` means one METHOD anywhere can make an unrelated dead
                # function look wired. It happened: `ToolPolicy.describe()`
                # arrived in Phase 5 and `profiles.describe`, which still has
                # no caller, silently left the unreferenced set.
                #
                # A false NEGATIVE, which is the dangerous direction for this
                # test: dead code looking wired is exactly what it exists to
                # catch. Same shape as the docstring bug this function was
                # already fixed for once - the check measured "the token
                # appears", not "the system calls it".
                #
                # So an attribute counts only when its receiver is a name an
                # import bound in this file, which is what a module-qualified
                # call looks like. `self.x` and `policy.x` no longer count.
                recv = node.value
                if isinstance(recv, ast.Name) and recv.id in module_names:
                    refs.add(node.attr)
            elif isinstance(node, ast.alias):        # from x import y [as z]
                refs.add(node.name.split(".")[-1])
                if node.asname:
                    refs.add(node.asname)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # getattr(obj, "name") and registry tables key off strings.
                # Kept deliberately: a string reference is a real one, and
                # dropping it would flag registered handlers as dead.
                refs.add(node.value)

    return {name: home for name, (home, _l) in defs.items() if name not in refs}


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
