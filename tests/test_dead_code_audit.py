from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agent"

KNOWN_INERT = {
    "import_external_skill":
        "External skills go through the same gate; none imported yet.",

    "reset":
        "test-only hook clearing net/politeness.py's robots and rate-limit "
        "caches, which are module-level and would otherwise leak between "
        "tests. No production call site BY DESIGN: a reset reachable from a "
        "real run is a way to bypass a site's crawl-delay.",
    "reset_injection_counter":
        "test-only helper for the BUCEPHALUS_INJECT_STALL fault-injection switch. "
        "Production has no call site BY DESIGN - a reset reachable from a "
        "real run is one more way for injection to affect a measurement.",

    "AccessGuard.dispatch": "ASGI middleware; Starlette calls it.",
    "BucephalusTUI.compose": "Textual lifecycle hook.",
    "BucephalusTUI.on_mount": "Textual lifecycle hook.",
    "BucephalusTUI.on_unmount": "Textual lifecycle hook.",
    "BucephalusTUI.on_ui_call": "Textual message handler, dispatched by name.",
    "BucephalusTUI.on_button_pressed": "Textual event handler, dispatched by name.",
    "BucephalusTUI.on_input_submitted": "Textual event handler, dispatched by name.",
    "BucephalusTUI.on_list_view_selected": "Textual event handler, dispatched by name.",
    "BucephalusTUI.action_sign": "Textual action, bound to `s` in BINDINGS.",
    "BucephalusTUI.action_send_back": "Textual action, bound to `b` in BINDINGS.",
    "BucephalusTUI.action_allow": "Textual action, bound to `a` in BINDINGS.",
    "BucephalusTUI.action_deny": "Textual action, bound to `d` in BINDINGS.",
    "BucephalusTUI.action_new": "Textual action, bound to `n` in BINDINGS.",
    "_Text.handle_data": "HTMLParser callback, invoked by the stdlib.",
    "_Text.handle_starttag": "HTMLParser callback, invoked by the stdlib.",
    "_Text.handle_endtag": "HTMLParser callback, invoked by the stdlib.",

    "ExternalMemoryProvider.recall": "tier 3 is an interface; nothing implements it.",
    "NoExternalProvider.recall": "the null implementation of that interface.",

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
                if node.decorator_list:
                    continue
                defs[node.name] = (f, node.lineno)
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if sub.name.startswith("_") or sub.decorator_list:
                        continue
                    methods.setdefault(f"{node.name}.{sub.name}", (f, sub.lineno))

    prod = list(SRC.rglob("*.py")) + list((ROOT / "bench").glob("*.py"))
    prod += [ROOT / "demo.py"]

    refs: set[str] = set()
    attr_refs: set[str] = set()
    for f in prod:
        if not f.exists():
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))

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
                recv = node.value
                if isinstance(recv, ast.Name) and recv.id in module_names:
                    refs.add(node.attr)
            elif isinstance(node, ast.alias):
                refs.add(node.name.split(".")[-1])
                if node.asname:
                    refs.add(node.asname)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                refs.add(node.value)
            if isinstance(node, ast.Attribute):
                attr_refs.add(node.attr)

    out = {name: home for name, (home, _l) in defs.items() if name not in refs}
    for qual, (home, _l) in methods.items():
        if qual.split(".", 1)[1] not in (refs | attr_refs):
            out[qual] = home
    return out


def test_no_newly_unreferenced_machinery():
    found = _unreferenced()
    new = sorted(set(found) - set(KNOWN_INERT))
    assert not new, (
        "these have no call site in production code:\n  "
        + "\n  ".join(f"{n}  ({found[n].relative_to(ROOT)})" for n in new)
        + "\n\nWire it, delete it, or add it to KNOWN_INERT with the reason. "
          "A test that a function works is not a test that the system calls it."
    )


def test_the_known_list_does_not_rot():
    found = _unreferenced()
    stale = sorted(set(KNOWN_INERT) - set(found))
    assert not stale, (
        f"these are now referenced and should leave KNOWN_INERT: {stale}")


def test_every_source_file_parses_cleanly():
    import warnings

    skip = {".git", "build", "dist", ".venv", "node_modules", "__pycache__"}
    bad = []
    for f in sorted(ROOT.rglob("*.py")):
        if skip & set(f.relative_to(ROOT).parts):
            continue
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                compile(f.read_text(encoding="utf-8"), str(f), "exec")
            except SyntaxError as e:
                bad.append(f"{f.relative_to(ROOT)}: {e.msg}")
        bad += [f"{f.relative_to(ROOT)}:{w.lineno}: {w.message}" for w in caught]
    assert not bad, "\n".join(bad)
