from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from agent.control.server import create_app
from test_console_reskin_guards import HTML, UI, _calls

PHRASE = "apply config"
RAW = {
    "profile": "mixed",
    "backends": {"cloud": {"kind": "mock", "model": "exec-1"}, "review": {"kind": "mock", "model": "rev-1"}},
    "autonomy": {"auto_review": False, "plan_required": False},
    "limits": {"max_steps": 24, "default_task_budget_usd": 0.5},
    "long_files": "pages",
}


@pytest.fixture
def cfg_file(ctx, tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("# my comment\n" + yaml.safe_dump(RAW), encoding="utf-8")
    ctx.config.source_path = str(p)
    return p


@pytest.fixture
def api(ctx, workspace, cfg_file):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def propose(api, **changes):
    return api.post("/config/propose", json={"changes": changes})


def apply(api, p, phrase=PHRASE, acks=()):
    return api.post("/config/apply", json={"hash": p["hash"], "phrase": phrase, "acks": list(acks)})


def test_the_view_lists_editable_settings_and_shows_the_rest_read_only(api, cfg_file):
    d = api.get("/config").json()
    assert d["path"] == str(cfg_file)
    ed = {e["path"]: e for e in d["editable"]}
    assert ed["limits.max_steps"]["value"] == 24 and ed["long_files"]["choices"] == ["pages", "elide", "whole"]
    assert ed["backends.cloud.model"]["value"] == "exec-1" and ed["backends.review.model"]["value"] == "rev-1"
    assert not any(p.startswith(("autonomy", "mechanisms", "hooks", "mcp_servers", "channels", "gate", "profile"))
                   for p in ed)
    assert "autonomy.plan_required" in {r["path"] for r in d["readonly"]}
    assert d["profile"]["name"] == "mixed"


def test_a_proposal_shows_the_diff_and_changes_nothing(api, cfg_file):
    before = cfg_file.read_text(encoding="utf-8")
    p = propose(api, **{"limits.max_steps": 30}).json()
    assert p["error"] is None and p["diff"] == [{"path": "limits.max_steps", "old": 24, "new": 30}]
    assert p["loosens"] == [] and p["hash"]
    assert cfg_file.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("path", ["autonomy.plan_required", "autonomy.human_only_tiers", "profile", "root",
                                  "mcp_servers.x.argv", "hooks.pre_tool", "channels.telegram.allowed_senders",
                                  "gate.max_p_value", "backends.cloud.endpoint", "backends.ghost.model", "nope"])
def test_a_setting_outside_the_editable_set_is_refused(api, path):
    r = propose(api, **{path: "x"})
    assert r.status_code == 400 and "not editable" in r.json()["detail"]


@pytest.mark.parametrize("changes", [{"limits.max_steps": "lots"}, {"limits.max_steps": -3},
                                     {"limits.max_steps": 0}, {"long_files": "everything"},
                                     {"backends.cloud.model": ""}, {"backends.cloud.model": "a\nb"}])
def test_an_invalid_value_is_refused_before_any_write(api, cfg_file, changes):
    before = cfg_file.read_text(encoding="utf-8")
    r = propose(api, **changes)
    assert r.status_code == 400 or r.json()["error"]
    assert cfg_file.read_text(encoding="utf-8") == before


def test_apply_needs_the_hash_and_the_typed_phrase(api, cfg_file):
    p = propose(api, **{"limits.max_steps": 30}).json()
    before = cfg_file.read_text(encoding="utf-8")
    assert apply(api, p, phrase="yes").status_code == 400
    assert apply(api, p, phrase="").status_code == 400
    assert api.post("/config/apply", json={"hash": "0" * 64, "phrase": PHRASE, "acks": []}).status_code == 404
    assert cfg_file.read_text(encoding="utf-8") == before


def test_apply_writes_atomically_keeps_a_backup_and_leaves_the_running_config(ctx, api, cfg_file):
    p = propose(api, **{"limits.max_steps": 30}).json()
    r = apply(api, p)
    assert r.status_code == 200, r.text
    assert yaml.safe_load(cfg_file.read_text(encoding="utf-8"))["limits"]["max_steps"] == 30
    backups = list(cfg_file.parent.glob("config.bak-*.yaml"))
    assert len(backups) == 1 and yaml.safe_load(backups[0].read_text(encoding="utf-8"))["limits"]["max_steps"] == 24
    assert not list(cfg_file.parent.glob("*.part")) and not list(cfg_file.parent.glob("*.tmp"))
    assert ctx.config.limits.max_steps != 30, "the running config changes at the next start, never in flight"
    assert r.json()["takes_effect"] == "next start"
    assert api.get("/config").json()["backups"]


def test_a_file_changed_since_the_proposal_is_not_overwritten(api, cfg_file):
    p = propose(api, **{"limits.max_steps": 30}).json()
    cfg_file.write_text(cfg_file.read_text(encoding="utf-8") + "\n# edited by hand\n", encoding="utf-8")
    assert apply(api, p).status_code == 409
    assert "limits" in yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert yaml.safe_load(cfg_file.read_text(encoding="utf-8"))["limits"]["max_steps"] == 24


def test_a_proposal_is_used_once(api):
    p = propose(api, **{"limits.max_steps": 30}).json()
    assert apply(api, p).status_code == 200
    assert apply(api, p).status_code == 404


@pytest.mark.parametrize("path,new,why", [("limits.max_steps", 500, "10"), ("limits.default_task_budget_usd", 50.0, "10")])
def test_raising_a_limit_tenfold_is_flagged_and_needs_its_own_ack(api, cfg_file, path, new, why):
    p = propose(api, **{path: new}).json()
    assert [f["path"] for f in p["loosens"]] == [path] and why in p["loosens"][0]["why"]
    assert apply(api, p).status_code == 400, "no ack, no write"
    assert apply(api, p, acks=["limits.something_else"]).status_code == 400
    assert apply(api, p, acks=[path]).status_code == 200


def test_lowering_a_limit_is_not_a_loosening(api):
    assert propose(api, **{"limits.max_steps": 3}).json()["loosens"] == []


def test_a_reviewer_that_becomes_the_executors_model_is_flagged(api):
    p = propose(api, **{"backends.review.model": "exec-1"}).json()
    assert [f["path"] for f in p["loosens"]] == ["backends.review.model"]


def test_a_proposal_the_loader_would_refuse_is_reported(api, monkeypatch):
    from agent import profiles

    def refuse(*a, **k):
        raise profiles.ProfileViolation("would not be local")

    monkeypatch.setattr("agent.config.enforce", refuse, raising=False)
    monkeypatch.setattr(profiles, "enforce", refuse)
    p = propose(api, **{"limits.max_steps": 30})
    assert p.status_code == 400 or p.json()["error"]


def test_no_token_no_config(ctx, workspace, cfg_file):
    c = TestClient(create_app(ctx), base_url="http://127.0.0.1")
    for r in (c.get("/config"), c.post("/config/propose", json={"changes": {}}),
              c.post("/config/apply", json={"hash": "x", "phrase": PHRASE, "acks": []})):
        assert r.status_code in (401, 403)


def test_without_a_config_file_nothing_is_editable(ctx, workspace):
    ctx.config.source_path = ""
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    assert c.get("/config").json()["editable"] == []
    assert c.post("/config/propose", json={"changes": {"limits.max_steps": 3}}).status_code == 409


def test_a_backup_is_named_so_the_protected_config_glob_covers_it(api, cfg_file):
    from fnmatch import fnmatch

    apply(api, propose(api, **{"limits.max_steps": 30}).json())
    assert all(fnmatch(b.name, "config*.yaml") for b in cfg_file.parent.glob("config.bak-*.yaml"))


def test_the_page_calls_only_its_routes():
    assert {("GET", "/config"), ("POST", "/config/propose"), ("POST", "/config/apply")} <= _calls(UI)
    assert 'data-pane="config"' in HTML


def test_environment_values_in_the_config_are_never_returned(ctx, api):
    from agent.config import HookConfig, McpServerConfig

    ctx.config.mcp_servers = {"s": McpServerConfig(argv=["x"], env={"API_TOKEN": "S3CRET-ENV-VALUE"})}
    ctx.config.hooks.pre_tool = [HookConfig(argv=["y"], env={"HOOK_KEY": "S3CRET-HOOK-VALUE"})]
    r = api.get("/config")
    assert "S3CRET" not in r.text
    rows = {x["path"]: x["value"] for x in r.json()["readonly"]}
    assert rows["mcp_servers.s.env"] == "API_TOKEN" and "HOOK_KEY" in str(rows)
