from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.config import BackendConfig
from agent.control.server import create_app
from agent.models import local

UI = (Path(__file__).resolve().parents[1] / "src" / "agent" / "control" / "ui.js").read_text(encoding="utf-8")
HTML = (Path(__file__).resolve().parents[1] / "src" / "agent" / "control" / "ui.html").read_text(encoding="utf-8")


class FakeOllama:
    def __init__(self):
        self.installed = ["granite4.1:3b", "granite4.1:8b"]
        self.loaded: dict[str, str] = {}
        self.context_length = 16384
        self.generated: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body):
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send({"models": [{"name": m, "size": 2_000_000_000} for m in outer.installed]})
                elif self.path == "/api/ps":
                    self._send({"models": [{"name": m, "size": 3_000_000_000, "expires_at": e,
                                           "context_length": outer.context_length}
                                           for m, e in outer.loaded.items()]})
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.generated.append(body)
                if body.get("keep_alive") in (0, "0"):
                    outer.loaded.pop(body["model"], None)
                else:
                    outer.loaded[body["model"]] = "2026-10-08T12:00:00Z"
                self._send({"done": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    f = FakeOllama()
    yield f
    f.close()


@pytest.fixture
def api(ctx, workspace, fake):
    from agent.models.ollama import OllamaBackend

    cfg = BackendConfig(kind="ollama", model="granite4.1:3b", endpoint=fake.endpoint, model_server="ollama")
    ctx.config.backends["cloud"] = cfg
    ctx.backends["cloud"] = OllamaBackend("cloud", cfg)
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_the_console_lists_installed_and_running_models(api, fake):
    fake.loaded["granite4.1:8b"] = "2026-10-08T12:00:00Z"
    d = api.get("/models").json()
    srv = next(s for s in d["servers"] if s["endpoint"] == fake.endpoint)
    assert srv["up"] and [m["name"] for m in srv["installed"]] == fake.installed
    assert [m["name"] for m in srv["loaded"]] == ["granite4.1:8b"]
    assert d["executor"] == {"slot": "cloud", "model": "granite4.1:3b", "endpoint": fake.endpoint, "local": True}


def test_run_model_loads_it_and_keeps_it_warm(api, fake):
    r = api.post("/models/run", json={"model": "granite4.1:8b"})
    assert r.status_code == 200, r.text
    assert fake.generated[-1]["model"] == "granite4.1:8b" and fake.generated[-1]["keep_alive"] == "30m"
    assert "granite4.1:8b" in fake.loaded
    assert api.post("/models/unload", json={"model": "granite4.1:8b"}).status_code == 200
    assert "granite4.1:8b" not in fake.loaded


def test_a_model_that_is_not_installed_is_refused(api, fake):
    r = api.post("/models/run", json={"model": "llama-giant:405b"})
    assert r.status_code == 400 and "not installed" in r.json()["detail"]
    assert not fake.generated


def test_selecting_a_model_changes_what_the_next_task_runs_on(api, ctx, fake):
    r = api.post("/models/select", json={"model": "granite4.1:8b"})
    assert r.status_code == 200, r.text
    assert ctx.backends["cloud"].model == "granite4.1:8b" and ctx.backends["cloud"].cfg.model == "granite4.1:8b"
    assert api.get("/models").json()["executor"]["model"] == "granite4.1:8b"
    assert api.post("/models/select", json={"model": "nope:1b"}).status_code == 400


def test_the_model_is_not_switched_under_a_running_task(api, monkeypatch):
    import agent.control.server as srv

    class Alive:
        def is_alive(self):
            return True

    monkeypatch.setitem(srv._runs, "t_busy", Alive())
    r = api.post("/models/select", json={"model": "granite4.1:8b"})
    assert r.status_code == 409


def test_the_users_own_ollama_is_never_managed(ctx):
    cfg = BackendConfig(kind="ollama", model="m", endpoint="http://localhost:11434", model_server="ollama")
    with pytest.raises(local.Refused, match="11434"):
        local.guard("http://localhost:11434", [cfg])
    with pytest.raises(local.Refused, match="11434"):
        local.guard("http://127.0.0.1:11434", [cfg])
    with pytest.raises(local.Refused, match="not a model server"):
        local.guard("http://127.0.0.1:11435", [cfg])


def test_a_stopped_server_is_started_like_ollama_serve_ps1(monkeypatch, tmp_path):
    started = {}

    class P:
        pid = 4242

        def poll(self):
            return None

    def popen(argv, **kw):
        started.update(argv=argv, env=kw["env"])
        return P()

    monkeypatch.setattr(local.shutil, "which", lambda name: "C:/ollama/ollama.exe")
    monkeypatch.setattr(local.subprocess, "Popen", popen)
    ups = iter([False, False, True])
    monkeypatch.setattr(local, "is_up", lambda endpoint: next(ups))
    monkeypatch.setattr(local.proc, "kill_tree", lambda pid, **kw: started.setdefault("killed", pid))
    (tmp_path / "ollama-models").mkdir()
    local.start_server("http://127.0.0.1:11435", tmp_path / "project")
    env = started["env"]
    assert started["argv"] == ["C:/ollama/ollama.exe", "serve"]
    assert env["OLLAMA_HOST"] == "127.0.0.1:11435" and env["OLLAMA_KEEP_ALIVE"] == "30m"
    assert env["OLLAMA_CONTEXT_LENGTH"] == "16384"
    assert Path(env["OLLAMA_MODELS"]) == tmp_path / "ollama-models"
    local.stop_owned()
    assert started["killed"] == 4242


def test_the_composer_has_a_model_picker_and_a_run_button():
    assert 'id="modelPick"' in HTML and 'data-action="runModel"' in HTML
    assert '"/models/run"' in UI and '"/models/select"' in UI


def test_only_a_declared_model_server_is_managed():
    plain = BackendConfig(kind="ollama", model="m", endpoint="http://127.0.0.1:11435")
    over_v1 = BackendConfig(kind="openai", model="m", base_url="http://127.0.0.1:11435/v1", model_server="ollama")
    assert local.server_of(plain) is None
    assert local.server_of(over_v1) == "http://127.0.0.1:11435"
    assert local.endpoints([plain, over_v1]) == ["http://127.0.0.1:11435"]


def test_a_backend_without_a_model_server_has_no_run_button(api, ctx):
    ctx.backends["cloud"].cfg.model_server = None
    assert api.get("/models").json()["executor"]["local"] is False
    assert api.post("/models/run", json={"model": "granite4.1:3b"}).status_code == 400


def _window(api, ctx, usable):
    ctx.backends["cloud"].cfg.usable_context_window = usable


def test_a_server_window_smaller_than_the_configured_one_is_reported(api, ctx, fake):
    _window(api, ctx, 16384)
    fake.context_length = 4096
    fake.loaded["granite4.1:3b"] = "2026-10-08T12:00:00Z"
    ex = api.get("/models").json()["executor"]
    assert ex["window"] == {"server": 4096, "configured": 16384}
    assert "4096" in ex["warning"] and "16384" in ex["warning"]


def test_a_matching_or_unknown_window_raises_no_warning(api, ctx, fake):
    _window(api, ctx, 16384)
    assert "warning" not in api.get("/models").json()["executor"]
    fake.loaded["granite4.1:3b"] = "2026-10-08T12:00:00Z"
    assert "warning" not in api.get("/models").json()["executor"]
    fake.context_length = 32768
    assert "warning" not in api.get("/models").json()["executor"]


def test_run_model_says_when_the_window_is_smaller(api, ctx, fake):
    _window(api, ctx, 16384)
    fake.context_length = 4096
    r = api.post("/models/run", json={"model": "granite4.1:3b"}).json()
    assert "4096" in r["warning"]
