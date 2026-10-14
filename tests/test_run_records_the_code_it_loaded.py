from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import agent
from agent import provenance

ROOT = Path(__file__).resolve().parents[1]


def test_the_identity_is_read_from_the_loaded_modules():
    ident = provenance.code_identity()
    assert ident["harness_version"] == agent.HARNESS_VERSION and ident["toolset_version"] == agent.TOOLSET_VERSION
    assert re.fullmatch(r"[0-9a-f]{64}", ident["src_sha256"])
    assert set(ident) >= {"git_commit", "git_dirty", "src_files"} and ident["src_files"] > 50


def test_the_hash_covers_the_source_that_is_loaded(tmp_path, monkeypatch):
    pkg = tmp_path / "agent"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n", encoding="utf-8")
    before = provenance.src_digest(pkg)
    (pkg / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert provenance.src_digest(pkg) != before
    (pkg / "b.py").write_text("", encoding="utf-8")
    assert provenance.src_digest(pkg)[1] == 2


def test_a_child_process_reports_its_own_code_not_the_parents(tmp_path):
    other = tmp_path / "src" / "agent"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("HARNESS_VERSION = 99\nTOOLSET_VERSION = 98\n", encoding="utf-8")
    code = "from agent import HARNESS_VERSION; print(HARNESS_VERSION)"
    r = subprocess.run([sys.executable, "-c", code], env={"PYTHONPATH": str(tmp_path / "src"), "PATH": ""},  # noqa: S603
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.stdout.strip() == "99"


@pytest.mark.parametrize("name", ["run.py", "noise.py", "ladder.py", "profile_run.py"])
def test_every_measurement_record_carries_the_identity(name):
    src = (ROOT / "bench" / name).read_text(encoding="utf-8")
    assert "code_identity()" in src, f"bench/{name} writes a record without the identity of the code it ran"


def test_a_profile_run_summary_carries_it(tmp_path):
    sys.path.insert(0, str(ROOT / "bench"))
    import profile_run

    out = tmp_path
    (out / "paired.json").write_text("{}", encoding="utf-8")
    summary = profile_run.summarise(out, 0)
    assert summary["code"]["harness_version"] == agent.HARNESS_VERSION
    json.dumps(summary)


def test_the_version_must_move_when_the_prompt_does():
    from agent.ledger.prefix import system_contract
    from agent.execute.loop import (ACTION_SCHEMA_HINT, ACTION_SCHEMA_HINT_NO_CODE, ACTION_TAG_HINT, BODY_NOTE,
                                    CARRY_NOTE, EVALUATOR_PREFIX, EVALUATOR_RETURN, NATIVE_HINT, READ_FIRST_NOTE,
                                    STUCK_SAY, UNREAD_PAGES_NOTE, VERIFY_NOTE)
    from agent.ledger.compact import EVIDENCE_HEAD
    from agent.execute.tools import apply_profile, default_registry
    from agent.plan.planner import PLANNER_PREFIX

    parts = [system_contract(True), system_contract(False), system_contract(False, compact=True), BODY_NOTE,
             CARRY_NOTE, NATIVE_HINT, EVALUATOR_PREFIX, EVALUATOR_RETURN, EVIDENCE_HEAD, *STUCK_SAY.values(),
             ACTION_SCHEMA_HINT, ACTION_SCHEMA_HINT_NO_CODE,
             ACTION_TAG_HINT, READ_FIRST_NOTE, PLANNER_PREFIX, UNREAD_PAGES_NOTE, VERIFY_NOTE]
    for profile in ("full", "patch", "small"):
        parts.append(apply_profile(default_registry(), profile).manual())
    digest = hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()
    golden = json.loads((ROOT / "tests" / "harness_prompts.json").read_text(encoding="utf-8"))
    assert str(agent.HARNESS_VERSION) in golden, (
        f"HARNESS_VERSION {agent.HARNESS_VERSION} has no entry in tests/harness_prompts.json: add its digest {digest}")
    assert golden[str(agent.HARNESS_VERSION)] == digest, (
        f"the prompt bytes changed without a HARNESS_VERSION bump (digest now {digest}). Bump the version and add "
        "the new digest under it; a run recorded between the change and the bump would name the wrong harness")
