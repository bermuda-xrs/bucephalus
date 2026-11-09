from __future__ import annotations

import os
import stat
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

from agent import keystore
from agent.config import BackendConfig
from agent.control import system
from agent.control.server import create_app
from test_console_reskin_guards import HTML, UI, _calls

SECRET = "sk-S3CRET-VALUE-1234"


@pytest.fixture
def api(ctx, workspace, monkeypatch):
    monkeypatch.delenv("BUCEPHALUS_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("REMOTE_KEY", raising=False)
    ctx.config.backends["remote"] = BackendConfig(kind="openai", model="m", endpoint="https://x.example/v1",
                                                  api_key_env="REMOTE_KEY")
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_only_names_the_config_declares_are_listed_and_none_is_set(api):
    d = api.get("/keys").json()["keys"]
    assert {k["name"] for k in d} == {"BUCEPHALUS_TELEGRAM_TOKEN", "REMOTE_KEY"}
    assert all(k["set"] is False and k["source"] is None for k in d)
    assert {k["name"]: k["used_by"] for k in d}["REMOTE_KEY"] == "backend remote"


def test_a_key_is_stored_and_never_read_back(api):
    r = api.put("/keys/REMOTE_KEY", json={"value": SECRET})
    assert r.status_code == 200 and SECRET not in r.text
    listed = api.get("/keys")
    assert SECRET not in listed.text and "1234" not in listed.text
    got = {k["name"]: k for k in listed.json()["keys"]}["REMOTE_KEY"]
    assert got["set"] is True and got["source"] == "console"
    assert keystore.get("REMOTE_KEY") == SECRET


def test_a_name_the_config_does_not_declare_is_refused(api):
    for name in ("PATH", "LD_PRELOAD", "NOT_DECLARED"):
        assert api.put(f"/keys/{name}", json={"value": "x"}).status_code == 404
        assert api.delete(f"/keys/{name}").status_code == 404
    assert keystore.get("PATH") == os.environ.get("PATH")


@pytest.mark.parametrize("value", ["", "  ", "a\nb", "a\rb", "x" * 600])
def test_a_malformed_value_is_refused(api, value):
    assert api.put("/keys/REMOTE_KEY", json={"value": value}).status_code == 400
    assert keystore.get("REMOTE_KEY") is None


def test_removing_a_key_forgets_it(api):
    api.put("/keys/REMOTE_KEY", json={"value": SECRET})
    assert api.delete("/keys/REMOTE_KEY").status_code == 200
    assert keystore.get("REMOTE_KEY") is None
    assert not any(k["set"] for k in api.get("/keys").json()["keys"])


def test_the_environment_wins_and_is_reported_as_such(api, monkeypatch):
    api.put("/keys/REMOTE_KEY", json={"value": SECRET})
    monkeypatch.setenv("REMOTE_KEY", "from-env")
    assert keystore.get("REMOTE_KEY") == "from-env"
    assert {k["name"]: k for k in api.get("/keys").json()["keys"]}["REMOTE_KEY"]["source"] == "environment"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_key_file_and_its_folder_are_private(api):
    api.put("/keys/REMOTE_KEY", json={"value": SECRET})
    assert stat.S_IMODE(keystore.path().stat().st_mode) == 0o600
    assert stat.S_IMODE(keystore.path().parent.stat().st_mode) & 0o077 == 0


def test_keys_are_refused_inside_a_synced_folder(api, monkeypatch):
    monkeypatch.setattr(keystore, "sync_root", lambda p: p)
    r = api.put("/keys/REMOTE_KEY", json={"value": SECRET})
    assert r.status_code == 409 and SECRET not in r.text
    assert keystore.get("REMOTE_KEY") is None


def test_a_backup_never_holds_a_key(api):
    api.put("/keys/REMOTE_KEY", json={"value": SECRET})
    name = api.post("/system/backup").json()["name"]
    with zipfile.ZipFile(system.backups_dir() / name) as z:
        assert not any("key" in n.lower() for n in z.namelist())
        assert all(SECRET.encode() not in z.read(n) for n in z.namelist())


def test_the_backends_and_the_bot_read_keys_through_the_store(api, ctx):
    from agent.models.openai_compat import OpenAICompatibleBackend

    api.put("/keys/REMOTE_KEY", json={"value": SECRET})
    b = OpenAICompatibleBackend("remote", ctx.config.backends["remote"])
    assert b._headers()["Authorization"] == "Bearer " + SECRET


def test_the_page_calls_only_its_routes_and_never_shows_a_value():
    assert {("GET", "/keys"), ("PUT", "/keys/{}"), ("DELETE", "/keys/{}")} <= _calls(UI)
    assert 'data-pane="keys"' in HTML
    block = UI[UI.index("async keys()"):]
    block = block[:block.index("\n  async ")]
    assert 'type="password"' in block and 'autocomplete="off"' in block and ".value" not in block


def test_the_agent_cannot_read_or_write_the_key_file(ctx, api):
    from agent.security.capabilities import state_paths

    api.put("/keys/REMOTE_KEY", json={"value": SECRET})
    assert keystore.path().resolve() in {p.resolve() for p in state_paths(ctx)}


def test_a_failed_write_leaves_no_staging_file_and_none_has_a_fixed_name(api, monkeypatch):
    import json as _json

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(_json, "dump", boom)
    with pytest.raises(OSError):
        keystore.put("REMOTE_KEY", SECRET)
    assert list(keystore.path().parent.glob("keys*")) == []
    monkeypatch.undo()
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append(str(a)), real(a, b))[1])
    keystore.put("REMOTE_KEY", SECRET)
    assert seen and not seen[0].endswith("keys.json.tmp")
