"""Fixtures for the iteration-3 audit probes.

Reuses tests/conftest.py (loaded under another name) so the probes build
contexts exactly as the suite does, and adds one more guard: ALPHA_DATA_DIR
and ALPHA_CONFIG_DIR point at scratch directories for every probe, so
nothing here - in process or in a subprocess - can reach the operator's real
user directories.

Run:  python -m pytest docs/evidence/audit-iteration-3 -q -p no:cacheprovider -n 0
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location("_alpha_suite_conftest", ROOT / "tests" / "conftest.py")
_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_suite)

make_config = _suite.make_config
_user_dirs_are_scratch = _suite._user_dirs_are_scratch      # autouse: config dir + platformdirs data dir
workspace = _suite.workspace
ctx = _suite.ctx
task = _suite.task


@pytest.fixture(autouse=True)
def _data_dir_env_is_scratch(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("ALPHA_DATA_DIR", str(tmp_path_factory.mktemp("alpha_data_env")))
