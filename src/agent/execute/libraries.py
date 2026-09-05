"""Sandbox libraries — what model-written code may import.

TWO EXTENSION SURFACES, DELIBERATELY SEPARATE
---------------------------------------------

  * a **TOOL** is a `ToolSpec` the agent loop dispatches. The model asks for
    it by name, the gate checks the arguments' taint, a receipt is written.
    That surface is `execute/tools.py`.

  * a **SANDBOX LIBRARY** is something model-written code may `import` inside
    `run_code`. The loop never sees it. The gate never sees it. It is not
    dispatched, it is simply *available*.

Conflating them would be a security error, not a taxonomy quibble: a tool call
passes through the capability gate and a library import does not. Anything
reachable by import is reachable without a gate decision, which is why the
declarations below exist.

WHAT A LIBRARY MUST DECLARE
---------------------------

The sandbox currently makes three promises: no network, no writes outside
`artifact_scope`, no subprocesses. A library that breaks any of them widens
the sandbox for every task that follows, silently, forever after.

So each entry declares all three explicitly. `register()` REFUSES anything
answering yes, and `tests/test_sandbox_libraries.py` holds that line — the
point is that the next addition cannot quietly widen the sandbox, which is
exactly the kind of drift this project keeps finding after the fact.

The declarations are claims by the registrar, not proofs. A library that lied
would still get in. What the refusal buys is that widening the sandbox has to
be *deliberate and visible in a diff*, rather than a side effect of adding a
convenient package.
"""

from __future__ import annotations

from dataclasses import dataclass


class LibraryRefused(ValueError):
    """A library declaring a capability the sandbox does not grant."""


@dataclass(frozen=True)
class SandboxLibrary:
    """One importable library, with its capability declaration.

    `version` is PINNED. An unpinned library means two runs can import
    different code while reporting the same TOOLSET_VERSION, which is the
    comparison hazard TOOLSET_VERSION exists to prevent.
    """

    name: str
    version: str
    purpose: str
    uses_network: bool = False
    writes_outside_scope: bool = False
    spawns_subprocesses: bool = False
    manual_note: str = ""

    def declaration(self) -> str:
        return (f"{self.name}=={self.version} "
                f"(network={self.uses_network}, "
                f"writes_outside_scope={self.writes_outside_scope}, "
                f"subprocesses={self.spawns_subprocesses})")


class LibraryRegistry:
    def __init__(self) -> None:
        self._libs: dict[str, SandboxLibrary] = {}

    def register(self, lib: SandboxLibrary) -> SandboxLibrary:
        """Add a library, or refuse it.

        Refusal is the whole point. The three checks below are the sandbox's
        stated guarantees; a library that needs one of them relaxed is a
        decision someone has to make explicitly, not something that arrives
        with a convenient package.
        """
        broken = [
            n for n, v in (
                ("network access", lib.uses_network),
                ("writes outside artifact_scope", lib.writes_outside_scope),
                ("subprocess spawning", lib.spawns_subprocesses),
            ) if v
        ]
        if broken:
            raise LibraryRefused(
                f"{lib.name} declares {' and '.join(broken)}, which the "
                "sandbox does not grant. Adding it would widen the sandbox "
                "for every task that follows. If this is genuinely wanted, "
                "the sandbox's guarantees have to change first - and that is "
                "a deliberate decision, not a registration.")
        if not lib.version:
            raise LibraryRefused(
                f"{lib.name} has no pinned version. Two runs could then "
                "import different code under the same TOOLSET_VERSION.")
        self._libs[lib.name] = lib
        return lib

    def all(self) -> list[SandboxLibrary]:
        return [self._libs[k] for k in sorted(self._libs)]

    def names(self) -> list[str]:
        return sorted(self._libs)

    def manual(self) -> str:
        """Lines for the tool manual the model sees. Must be deterministic."""
        out = []
        for lib in self.all():
            note = f" {lib.manual_note}" if lib.manual_note else ""
            out.append(f"  import {lib.name}  ({lib.purpose}){note}")
        return "\n".join(out)

    def provenance(self) -> list[str]:
        return [lib.declaration() for lib in self.all()]


# ---------------------------------------------------------------------------
# The registered set. Adding an entry here changes TOOLSET_VERSION.
# ---------------------------------------------------------------------------

REGISTRY = LibraryRegistry()

REGISTRY.register(SandboxLibrary(
    name="sympy",
    version="1.13.3",
    purpose="exact symbolic mathematics",
    uses_network=False,
    writes_outside_scope=False,
    spawns_subprocesses=False,
    manual_note=(
        "- prefer it over doing arithmetic in your head. "
        "sympy.sympify/solve/simplify are exact, and "
        "sympy.physics.units converts units without a manual factor."),
))
