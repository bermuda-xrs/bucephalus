"""Freeze the agent with PyInstaller (onedir). One command:

    python packaging/build.py --probe     # the D13 gate: sandbox tests, frozen

Run it from a BUILD venv that has the project installed (non-editable) plus
PyInstaller - never the measurement venv, whose toolset is part of the record
(TOOLSET_VERSION). Output goes outside the OneDrive folder, which would
otherwise sync ~200 MB of build churn.

The whole standard library is bundled, not what the analysis finds: the
sandbox runs MODEL-WRITTEN code through this binary as its interpreter, and a
model may import any stdlib module the unfrozen sandbox would have given it.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("ALPHA_BUILD_DIR", Path.home() / ".alpha-build"))

# Never importable or never wanted in a sandbox interpreter.
_SKIP = {"tkinter", "_tkinter", "turtle", "turtledemo", "idlelib", "test",
         "lib2to3", "ensurepip", "venv", "antigravity", "this", "__phello__"}


def _stdlib() -> list[str]:
    return sorted(m for m in sys.stdlib_module_names
                  if m not in _SKIP and not m.startswith("_test")
                  and importlib.util.find_spec(m) is not None)


def _size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 2**20


def build(name: str, entry: Path, extra: list[str]) -> Path:
    import PyInstaller.__main__

    args = [str(entry), "--noconfirm", "--onedir", "--console", "--name", name,
            "--distpath", str(OUT / "dist"), "--workpath", str(OUT / "work"),
            "--specpath", str(OUT), "--collect-submodules", "agent",
            "--collect-data", "agent", "--collect-all", "sympy",
            "--collect-all", "mpmath", *extra]
    for m in _stdlib():
        args += ["--hidden-import", m]
    t0 = time.time()
    PyInstaller.__main__.run(args)
    dist = OUT / "dist" / name
    print(f"\n{name}: {dist} - {_size_mb(dist):.0f} MB, {time.time() - t0:.0f} s")
    return dist


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--probe", action="store_true", required=True,
                   help="the D13 gate build")
    p.parse_args(argv)
    sep = os.pathsep
    build("alpha-probe", ROOT / "packaging" / "probe_entry.py",
          ["--collect-all", "pytest", "--collect-all", "_pytest",
           "--collect-all", "pluggy", "--collect-all", "iniconfig",
           "--add-data", f"{ROOT / 'tests'}{sep}tests"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
