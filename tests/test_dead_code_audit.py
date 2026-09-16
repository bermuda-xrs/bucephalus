"""Machinery with green tests and zero call sites. It grew back.

An audit early in this project found seven pieces of spec machinery that were
unit-tested and never called by the running agent. `test_wired_invariants.py`
exists because of it. A fresh audit on 2026-08-27 found nine more, so the
pattern is not a one-off — it is what happens by default when a milestone is
implemented, tested, and then not plugged in.

This test freezes the known set. A NEW unreferenced function fails it. The
existing ones are listed with why they are tolerated, so the list is a record
rather than a suppression.

The one that actually matters is called out in `KNOWN_INERT`:

  * `local_with_escalation` is never called, so escalation-by-disagreement
    (M6.3) does not happen; the router escalates on allowlist rules alone.

`record_holdout_gap` was the other, until it was retired with the generator
it policed (M11, 2026-09-15, FINDINGS 1.27).
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
    "local_with_escalation":
        "M6.3 escalation-by-disagreement never runs; the router escalates on "
        "allowlist rules only.",
    "freeze_skills":
        "A second implementation. `AgentContext.freeze_skills` is the one the "
        "loop calls; this module-level version in ledger/prefix.py has no "
        "caller. It looked wired only because the audit credited the METHOD "
        "call `ctx.freeze_skills(task)` to it.",

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

    # --- METHODS, visible since D22 was closed 2026-09-16 -----------------
    # Invoked by a framework, not by this project. False positives, named
    # so the list stays a record rather than a filter.
    "AccessGuard.dispatch": "ASGI middleware; Starlette calls it.",
    "AlphaTUI.compose": "Textual lifecycle hook.",
    "AlphaTUI.on_mount": "Textual lifecycle hook.",
    "AlphaTUI.on_unmount": "Textual lifecycle hook.",
    "AlphaTUI.on_button_pressed": "Textual event handler, dispatched by name.",
    "_Text.handle_data": "HTMLParser callback, invoked by the stdlib.",
    "_Text.handle_starttag": "HTMLParser callback, invoked by the stdlib.",
    "_Text.handle_endtag": "HTMLParser callback, invoked by the stdlib.",

    # Interface with no implementation, by design (A1 3.5).
    "ExternalMemoryProvider.recall": "tier 3 is an interface; nothing implements it.",
    "NoExternalProvider.recall": "the null implementation of that interface.",

    # Genuinely uncalled. Each is small, and each is a decision to leave it.
    "AgentContext.gate_queue": "candidates are read by status; this derives the same list for a caller that does not exist yet.",
    "Anchor.all_tasks": "helper; `alpha verify` iterates tasks from the task table.",
    "BackendBase.set_throughput": "the router reads observed throughput from telemetry instead.",
    "EventLog.last_of_type": "helper; every caller writes its own query.",
    "ToolRegistry.read_only": "the planner builds its read-only set from the registry directly.",
    "RunState.resolve_last_tool": "TUI helper for a view that was never built.",
    "UserStore.grounding_sources": "M11's grounding input. The generator was retired 2026-09-15 (FINDINGS 1.27); the console still stores the notes and nothing reads them.",
}


def _unreferenced() -> dict[str, Path]:
    defs = {}
    methods: dict[str, tuple[Path, int]] = {}
    for f in SRC.rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_") or node.name == "main":
                    continue
                if node.decorator_list:          # framework-registered
                    continue
                defs[node.name] = (f, node.lineno)
            # D22. The audit read module level only, so 128 public METHODS were
            # invisible to it - and a one-off sweep found 14 with no production
            # reference. A method is judged by NAME here: `x.name` anywhere in
            # production counts, whatever `x` is. That is weaker than the
            # receiver-aware rule above and deliberately so - the alternative
            # is every `self.x` call reading as dead. It still catches the case
            # this exists for: a method nothing ever names.
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if sub.name.startswith("_") or sub.decorator_list:
                        continue
                    methods.setdefault(f"{node.name}.{sub.name}", (f, sub.lineno))

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
    attr_refs: set[str] = set()
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
            if isinstance(node, ast.Attribute):
                attr_refs.add(node.attr)          # D22: any receiver counts

    out = {name: home for name, (home, _l) in defs.items() if name not in refs}
    for qual, (home, _l) in methods.items():
        if qual.split(".", 1)[1] not in (refs | attr_refs):
            out[qual] = home
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
    for name in ("local_with_escalation",):
        assert name in KNOWN_INERT
        assert "INERT" in KNOWN_INERT[name] or "never" in KNOWN_INERT[name]
