from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _name(req: str) -> str:
    return re.split(r"[<>=!~\[ ;]", req.strip().strip('"'), maxsplit=1)[0].lower().replace("_", "-")


def test_setup_installs_every_dependency_pyproject_declares():
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = {_name(r) for r in proj["dependencies"] + proj["optional-dependencies"]["dev"]
                + proj["optional-dependencies"]["desktop"]}
    setup = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    line = setup[setup.index("pip install --quiet `"):setup.index("Write-Host", setup.index("pip install --quiet `"))]
    installed = {_name(w) for w in line.replace("`", " ").split() if not w.startswith(("-", "&", '"$venv'))}
    assert declared <= installed, sorted(declared - installed)


def test_the_desktop_app_declares_the_window_library_it_imports():
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "pywebview" in {_name(r) for r in proj["optional-dependencies"].get("desktop", [])}
