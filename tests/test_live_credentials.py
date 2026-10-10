from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from agent.channel.telegram import API, HttpTransport, TransportError
from agent.config import BackendConfig
from agent.models.anthropic import AnthropicBackend

ROOT = Path(__file__).resolve().parents[1]
TELEGRAM_TOKEN = os.environ.get("BUCEPHALUS_TELEGRAM_TOKEN")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")


def anthropic_backends() -> dict[str, BackendConfig]:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return {n: BackendConfig.model_validate(b) for n, b in raw["backends"].items()
            if b.get("kind") == "anthropic"}


def exercise_telegram(transport) -> None:
    me = transport._call("getMe", {})  # noqa: SLF001
    assert me.get("is_bot") is True and me.get("username"), me
    assert isinstance(transport.get_updates(offset=0, timeout=0), list)
    with pytest.raises(TransportError):
        transport._call("bucephalusNoSuchMethod", {})  # noqa: SLF001


def exercise_anthropic(backend) -> None:
    got: list[str] = []
    stamps: list[float] = []
    resp = backend.complete("Reply with exactly one word: pong.", "ping",
                            on_chunk=lambda t: (got.append(t), stamps.append(time.time())))
    done = time.time()
    assert got, "no text reached the surface while the turn ran"
    assert "".join(got) == resp.text and resp.text.strip(), (got, resp.text)
    assert stamps[0] <= done
    assert resp.usage.input_tokens > 0 and resp.usage.output_tokens > 0, resp.usage
    assert resp.raw.get("stop_reason") in ("end_turn", "stop_sequence", "max_tokens"), resp.raw
    assert str(resp.raw.get("model", "")).startswith(backend.cfg.model), resp.raw


@pytest.mark.skipif(not TELEGRAM_TOKEN, reason="BUCEPHALUS_TELEGRAM_TOKEN is not set: "
                    "the live Telegram path stays UNVERIFIED (D26)")
def test_telegram_transport_live():
    exercise_telegram(HttpTransport(
        TELEGRAM_TOKEN, base=os.environ.get("BUCEPHALUS_TELEGRAM_API_BASE", API)))


@pytest.mark.skipif(not ANTHROPIC_KEY, reason="ANTHROPIC_API_KEY is not set: "
                    "the Anthropic adapter stays UNVERIFIED (D26)")
@pytest.mark.parametrize("name", sorted(anthropic_backends()))
def test_anthropic_streaming_live(name):
    exercise_anthropic(AnthropicBackend(name, anthropic_backends()[name]))


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.server.requests.append((self.path, body))
        status, ctype, payload = self.server.respond(self.path, body)
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def fake_server():
    servers: list = []

    def start(respond):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        srv.respond, srv.requests = respond, []
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def telegram_api(path: str, body: dict):
    method = path.rsplit("/", 1)[-1]
    result = {"getMe": {"id": 1, "is_bot": True, "username": "fake_bot"},
              "getUpdates": []}.get(method)
    ok = result is not None and path.startswith("/botfake-token/")
    return 200, "application/json", json.dumps(
        {"ok": ok, "result": result, "description": "fake: not found"}).encode()


def test_the_telegram_body_against_a_fake_api(fake_server):
    srv, base = fake_server(telegram_api)
    exercise_telegram(HttpTransport("fake-token", base=base))
    assert [p.rsplit("/", 1)[-1] for p, _ in srv.requests] == [
        "getMe", "getUpdates", "bucephalusNoSuchMethod"]
    assert srv.requests[1][1] == {"offset": 0, "timeout": 0}, "a live poll confirms nothing"


def _sse(model: str, pieces: list[str]) -> bytes:
    def ev(kind: str, data: dict) -> str:
        return f"event: {kind}\ndata: {json.dumps(data)}\n\n"

    out = [ev("message_start", {"type": "message_start", "message": {
        "id": "msg_fake", "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 1}}}),
        ev("content_block_start", {"type": "content_block_start", "index": 0,
                                   "content_block": {"type": "text", "text": ""}})]
    out += [ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "text_delta", "text": p}})
            for p in pieces]
    out += [ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ev("message_delta", {"type": "message_delta",
                                 "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                 "usage": {"output_tokens": 2}}),
            ev("message_stop", {"type": "message_stop"})]
    return "".join(out).encode()


def anthropic_api(path: str, body: dict):
    if not path.startswith("/v1/messages") or not body.get("stream"):
        return 400, "application/json", json.dumps({"type": "error", "error": {
            "type": "invalid_request_error", "message": "fake: streaming only"}}).encode()
    return 200, "text/event-stream", _sse(body["model"], ["po", "ng"])


@pytest.mark.parametrize("name", sorted(anthropic_backends()))
def test_the_anthropic_body_against_a_fake_api(fake_server, monkeypatch, name):
    srv, base = fake_server(anthropic_api)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    exercise_anthropic(AnthropicBackend(name, anthropic_backends()[name]))
    ((_, body),) = srv.requests
    assert body["model"] == anthropic_backends()[name].model and body["stream"] is True
