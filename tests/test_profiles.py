from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent.config import BackendConfig
from agent.context import AgentContext
from agent.execute.loop import run_task
from agent.execute.tools import PATCH_ONLY, SMALL_ONLY, default_registry, tool_apply_patch
from agent.kernel.tasks import create_task

from conftest import make_config

ROOT = Path(__file__).resolve().parent.parent
FULL_KIT = sorted(set(default_registry().names()) - set(SMALL_ONLY) - set(PATCH_ONLY))


def _profiled(tmp_path, profile):
    c = AgentContext(make_config(tmp_path))
    c.backends["cloud"].cfg.tool_profile = profile
    c.close()
    c = AgentContext(make_config(tmp_path), backends=c.backends)
    c.config.autonomy.approve_scoped_writes = True
    return c


def test_no_declared_profile_means_the_full_kit():
    assert BackendConfig(kind="mock", model="m").tool_profile == "full"


@pytest.mark.parametrize("path", sorted(ROOT.glob("config*.yaml")), ids=lambda p: p.name)
def test_no_shipped_config_makes_a_smaller_kit_the_default(path):
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for name, b in (raw.get("backends") or {}).items():
        if "tool_profile" not in b:
            assert BackendConfig(**b).tool_profile == "full", (path.name, name)


def test_a_context_with_no_profile_gets_every_full_kit_tool(ctx):
    assert ctx.tool_profile == "full"
    assert ctx.tools.names() == FULL_KIT
    assert {"edit_file", "run_code"} <= set(ctx.tools.names())
    assert not {"apply_patch", "search", "run_command", "update_plan"} & set(ctx.tools.names())
    assert ctx.code_as_action


def test_a_profile_on_another_slot_does_not_shrink_the_executor(tmp_path, workspace):
    c = AgentContext(make_config(tmp_path))
    c.backends["review"].cfg.tool_profile = "small"
    c.close()
    c = AgentContext(make_config(tmp_path), backends=c.backends)
    assert c.tool_profile == "full" and c.tools.names() == FULL_KIT
    c.close()


def test_the_edit_tool_follows_the_profile(tmp_path, workspace):
    seen = {}
    for p in ("full", "patch", "small"):
        c = _profiled(tmp_path, p)
        seen[p] = set(c.tools.names())
        c.close()
    assert "edit_file" in seen["full"] and "apply_patch" not in seen["full"]
    assert "apply_patch" in seen["patch"] and "edit_file" not in seen["patch"]
    assert not {"edit_file", "apply_patch"} & seen["small"] and "write_file" in seen["small"]
    assert seen["patch"] - {"apply_patch"} == seen["full"] - {"edit_file"}


def _local(c, **cfg):
    b = c.backends["cloud"]
    b.cfg.trust_tier = "local"
    for k, v in cfg.items():
        setattr(b.cfg, k, v)


def _first_call(c, task):
    t = create_task(c.conn, c.log, task.contract, task.budget)
    c.backends["cloud"].queue('{"kind": "final", "answer": "ok"}')
    run_task(c, t.id)
    return c.backends["cloud"].calls[0]


def test_constrained_decoding_only_for_a_declared_ollama_server(ctx, task):
    _local(ctx, model_server="ollama")
    assert "anyOf" in _first_call(ctx, task)["schema"]


def test_a_local_backend_without_an_ollama_server_keeps_the_generic_schema(ctx, task):
    _local(ctx, model_server=None)
    assert "anyOf" not in _first_call(ctx, task)["schema"]


def test_an_api_backend_gets_no_schema_at_all(ctx, task):
    assert ctx.backends["cloud"].cfg.trust_tier == "cloud"
    assert _first_call(ctx, task)["schema"] is None


PATCH = "@@\n-x = 1\n+x = 2\n y = 3\n"


@pytest.fixture
def patcher(tmp_path, workspace):
    c = _profiled(tmp_path, "patch")
    yield c
    c.close()


def test_apply_patch_replaces_by_context(patcher, task, workspace):
    (workspace / "a.py").write_text("x = 1\ny = 3\nz = 4\n", encoding="utf-8")
    r = tool_apply_patch(patcher, task, path="work/a.py", patch=PATCH)
    assert r["ok"] and (workspace / "a.py").read_text(encoding="utf-8") == "x = 2\ny = 3\nz = 4\n"


def test_apply_patch_is_all_or_nothing(patcher, task, workspace):
    (workspace / "a.py").write_text("x = 1\ny = 3\n", encoding="utf-8")
    bad = PATCH + "@@\n-gone = 0\n+gone = 1\n"
    r = tool_apply_patch(patcher, task, path="work/a.py", patch=bad)
    assert not r["ok"] and "not found" in r["error"]
    assert (workspace / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 3\n"


def test_apply_patch_refuses_an_ambiguous_hunk(patcher, task, workspace):
    (workspace / "a.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    r = tool_apply_patch(patcher, task, path="work/a.py", patch="@@\n-x = 1\n+x = 2\n")
    assert not r["ok"] and "2 times" in r["error"]


def test_apply_patch_creates_a_file_from_added_lines(patcher, task, workspace):
    r = tool_apply_patch(patcher, task, path="work/new.py", patch="@@\n+a = 1\n+b = 2\n")
    assert r["ok"] and (workspace / "new.py").read_text(encoding="utf-8") == "a = 1\nb = 2\n"


def test_apply_patch_goes_through_the_path_gate(patcher, task):
    t = create_task(patcher.conn, patcher.log, task.contract, task.budget)
    act = '<action tool="apply_patch">' + json.dumps({"path": "../outside.py", "patch": "@@\n+a = 1\n"}) + "</action>"
    patcher.backends["cloud"].queue(act, '<final>{"answer": "ok"}</final>')
    run_task(patcher, t.id)
    assert "outside artifact_scope" in patcher.backends["cloud"].calls[1]["tail"]


def test_a_patched_python_file_is_checked_too(patcher, task, workspace):
    (workspace / "b.py").write_text("x = 1\n", encoding="utf-8")
    t = create_task(patcher.conn, patcher.log, task.contract, task.budget)
    act = '<action tool="apply_patch">' + json.dumps({"path": "work/b.py", "patch": "@@\n-x = 1\n+x = (1\n"}) + "</action>"
    patcher.backends["cloud"].queue(act, '<final>{"answer": "ok"}</final>')
    run_task(patcher, t.id)
    assert "SyntaxError" in patcher.backends["cloud"].calls[1]["tail"]


@pytest.mark.parametrize("profile", ["full", "patch", "small"])
def test_the_checks_apply_to_every_profile(tmp_path, workspace, task, profile):
    c = _profiled(tmp_path, profile)
    t = create_task(c.conn, c.log, task.contract, task.budget)
    w = '<action tool="write_file">' + json.dumps({"path": "work/a.py", "content": "def f(:\n"}) + "</action>"
    c.backends["cloud"].queue(w, w, w, w, '<final>{"answer": "ok"}</final>')
    run_task(c, t.id)
    calls = c.backends["cloud"].calls
    assert "SyntaxError" in calls[1]["tail"]
    assert "change approach" in calls[4]["tail"].lower()
    c.close()
