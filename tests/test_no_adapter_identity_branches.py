from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.config import BackendConfig  # noqa: E402

ADAPTER_KINDS = set(BackendConfig.model_fields["kind"].annotation.__args__)

SEARCH_ROOTS = [ROOT / "src" / "agent", ROOT / "bench"]

ALLOWED = {
    "src/agent/models/registry.py":
        "build_backend IS the adapter registry. Mapping a kind to the class "
        "that implements it is the one place the mapping can exist.",
    "src/agent/profiles.py":
        "CLOUD_ONLY_KINDS is a kind->capability table: 'this kind reaches a "
        "provider by construction and has no local form'. It is OR'd with an "
        "endpoint-derived check that can only ADD to the cloud set, never "
        "clear it. Deliberately a hardcoded table rather than a config field, "
        "because a field would create a new way to get it wrong - someone "
        "writing reaches_network: false on an anthropic backend - and a "
        "security default that can be misconfigured downward is worse than "
        "one that cannot be edited at all.",
}


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _string_operands(node: ast.Compare) -> set[str]:
    out: set[str] = set()
    for side in [node.left, *node.comparators]:
        if isinstance(side, ast.Constant) and isinstance(side.value, str):
            out.add(side.value)
        elif isinstance(side, (ast.Tuple, ast.List, ast.Set)):
            for e in side.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    out.add(e.value)
    return out


def _touches_kind(node: ast.Compare) -> bool:
    for side in [node.left, *node.comparators]:
        if isinstance(side, ast.Attribute) and side.attr == "kind":
            return True
    return False


def find_identity_branches() -> list[str]:
    hits = []
    for root in SEARCH_ROOTS:
        for path in sorted(root.rglob("*.py")):
            rel = _rel(path)
            if rel in ALLOWED or "/archive/" in rel:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare) or not _touches_kind(node):
                    continue
                if _string_operands(node) & ADAPTER_KINDS:
                    hits.append(f"{rel}:{node.lineno}")
    return hits


def test_no_branch_on_adapter_identity_outside_the_registry():
    hits = find_identity_branches()
    assert not hits, (
        "a backend's ADAPTER TYPE is being used to decide behaviour at "
        f"{hits}. If the branch is asking about a CAPABILITY - does this "
        "backend need an action envelope, report cached tokens, honour "
        "num_ctx - declare it as a BackendConfig field and branch on that. "
        "Two adapters can reach the same server running the same weights, "
        "and a class-name test flips while nothing about the model changes."
    )


def test_the_allowlist_does_not_rot():
    stale = []
    for rel in ALLOWED:
        text = (ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(text)
        branches = any(
            isinstance(n, ast.Compare) and _touches_kind(n)
            and (_string_operands(n) & ADAPTER_KINDS)
            for n in ast.walk(tree)
        )
        names = any(
            isinstance(n, ast.Constant) and n.value in ADAPTER_KINDS
            for n in ast.walk(tree)
        )
        if not (branches or names):
            stale.append(rel)
    assert not stale, f"no longer branches on adapter identity: {stale}"


def test_the_kind_list_is_read_from_the_model_not_retyped():
    assert "openai" in ADAPTER_KINDS and "ollama" in ADAPTER_KINDS
    assert len(ADAPTER_KINDS) == len(BackendConfig.model_fields["kind"].annotation.__args__)


def test_the_three_known_instances_are_actually_fixed():
    loop = (ROOT / "src" / "agent" / "execute" / "loop.py").read_text(encoding="utf-8")
    review = (ROOT / "src" / "agent" / "plan" / "review.py").read_text(encoding="utf-8")
    profiles = (ROOT / "src" / "agent" / "profiles.py").read_text(encoding="utf-8")
    assert "needs_action_envelope" in loop
    assert "needs_action_envelope" in review
    assert "reaches_a_remote_provider" in profiles
