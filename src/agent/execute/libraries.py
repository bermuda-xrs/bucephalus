from __future__ import annotations

from dataclasses import dataclass


class LibraryRefused(ValueError):
    ...


@dataclass(frozen=True)
class SandboxLibrary:
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
        out = []
        for lib in self.all():
            note = f" {lib.manual_note}" if lib.manual_note else ""
            out.append(f"  import {lib.name}  ({lib.purpose}){note}")
        return "\n".join(out)

    def provenance(self) -> list[str]:
        return [lib.declaration() for lib in self.all()]


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
