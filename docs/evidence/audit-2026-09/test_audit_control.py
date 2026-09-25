"""AUDIT-2026-09 evidence: the control plane's access guard.

In-process TestClient only; nothing binds a socket. Green = demonstration.
"""
from __future__ import annotations

import html
import re
import secrets
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import make_config  # noqa: E402

from agent.context import AgentContext  # noqa: E402
from agent.control.server import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHA_TOKEN", raising=False)
    ctx = AgentContext(make_config(tmp_path))
    app = create_app(ctx)
    yield TestClient(app, base_url="http://127.0.0.1"), app.state.token
    ctx.close()


def test_F_unauthenticated_loopback_get_returns_the_token(client):
    """Any local process (any local USER, on Windows loopback) that can
    connect gets the bearer token by asking for '/': no credential needed.
    The guard's rationale covers cross-origin PAGES only."""
    c, token = client
    r = c.get("/")                      # no Authorization, no Origin
    assert r.status_code == 200
    assert token in r.text


def test_F_esc_is_not_safe_inside_js_string_in_attribute():
    """ui.html builds onclick="delSuite('${esc(s.id)}','${esc(s.name)}')".
    The browser HTML-decodes the attribute BEFORE parsing it as JS, so
    esc() round-trips a quote back into the script."""
    def esc(s):  # ui.html's esc, transliterated
        return re.sub(r"[&<>\"']", lambda m: {"&": "&amp;", "<": "&lt;", ">": "&gt;",
                                              '"': "&quot;", "'": "&#39;"}[m.group()], s)
    name = "x');/*breakout*/('"
    attr = f"delSuite('s_1','{esc(name)}')"
    js_seen = html.unescape(attr)       # what the JS engine receives
    assert js_seen == "delSuite('s_1','x');/*breakout*/('')"


def test_F_non_ascii_token_header_is_a_500_not_a_401(client):
    """secrets.compare_digest raises TypeError on non-ASCII str; the guard does
    not catch it, so a malformed credential becomes a server error."""
    c, _ = client
    # Headers are ASCII on the wire; the query string is not. Under uvicorn
    # the unhandled TypeError is a 500 plus a traceback in desktop.log.
    with pytest.raises(TypeError, match="non-ASCII"):
        c.get("/tasks", params={"token": "café"})


def test_F_console_is_framable(client):
    """GET / sends neither X-Frame-Options nor a CSP frame-ancestors. A
    navigation (iframe) request carries no Origin header, and its Host is the
    loopback name, so any page in the user's browser can frame the working,
    token-bearing console (clickjacking of 'Approve and run' / 'Allow this
    once'). Frame requests pass the guard exactly as below."""
    c, token = client
    r = c.get("/", headers={"sec-fetch-dest": "iframe", "sec-fetch-site": "cross-site"})
    assert r.status_code == 200 and token in r.text
    h = {k.lower() for k in r.headers}
    assert "x-frame-options" not in h
    assert "frame-ancestors" not in r.headers.get("content-security-policy", "")
