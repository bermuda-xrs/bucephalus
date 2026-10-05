from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("ALPHA_BUILD_DIR", Path.home() / ".alpha-build"))

_SKIP = {"tkinter", "_tkinter", "turtle", "turtledemo", "idlelib", "test",
         "lib2to3", "ensurepip", "venv", "antigravity", "this", "__phello__"}


def _stdlib() -> list[str]:
    return sorted(m for m in sys.stdlib_module_names
                  if m not in _SKIP and not m.startswith("_test")
                  and importlib.util.find_spec(m) is not None)


def _size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 2**20


def build(name: str, entry: Path, extra: list[str], console: bool = True) -> Path:
    import PyInstaller.__main__

    args = [str(entry), "--noconfirm", "--onedir",
            "--console" if console else "--windowed", "--name", name,
            "--distpath", str(OUT / "dist"), "--workpath", str(OUT / "work"),
            "--specpath", str(OUT), "--collect-submodules", "agent",
            "--collect-data", "agent",
            "--collect-submodules", "sympy", "--collect-submodules", "mpmath",
            "--exclude-module", "scipy", "--exclude-module", "numpy", *extra]
    for m in _stdlib():
        args += ["--hidden-import", m]
    t0 = time.time()
    PyInstaller.__main__.run(args)
    dist = OUT / "dist" / name
    print(f"\n{name}: {dist} - {_size_mb(dist):.0f} MB, {time.time() - t0:.0f} s")
    return dist


_TASK_PARTS = {"bench", "graders", "grading"}


def no_task_content(dist: Path) -> list[str]:
    bad = []
    for f in dist.rglob("*"):
        rel = f.relative_to(dist)
        if _TASK_PARTS & {p.lower() for p in rel.parts} or f.name == "verify.py":
            bad.append(str(rel))
        elif f.suffix == ".json" and f.is_file() and f.stat().st_size < 2**20:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(d, dict) and "goal" in d and "success_criterion" in d:
                bad.append(str(rel))
    return bad


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true", help="the D13 gate build")
    args = p.parse_args(argv)
    sep = os.pathsep
    if args.probe:
        build("alpha-probe", ROOT / "packaging" / "probe_entry.py",
              ["--collect-all", "pytest", "--collect-all", "_pytest",
               "--collect-all", "pluggy", "--collect-all", "iniconfig",
               "--add-data", f"{ROOT / 'tests'}{sep}tests"])
        return 0
    dist = build("alpha", ROOT / "packaging" / "app_entry.py",
                 ["--collect-all", "webview",
                  "--add-data", f"{ROOT / 'config.local.yaml'}{sep}."],
                 console=False)
    bad = no_task_content(dist)
    if bad:
        print("TASK CONTENT IN THE BUILD - refusing it:", *bad[:20], sep="\n  ")
        return 1
    print("no task content in the build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
