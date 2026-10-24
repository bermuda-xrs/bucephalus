from __future__ import annotations

import httpx
import pytest

from agent.net import fetch as F


def _client_with(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(F.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(F, "_is_public", lambda host: host.endswith("public.example"))


def test_a_redirect_to_a_private_address_is_refused_before_it_is_followed(monkeypatch):
    def handler(req):
        if req.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:9/secret"})
        raise AssertionError(f"followed the redirect to {req.url}")

    _client_with(monkeypatch, handler)
    with pytest.raises(F.FetchError, match="private"):
        F.fetch_with_provenance("http://public.example/x", polite=False)


def test_a_redirect_between_public_hosts_is_followed_and_recorded(monkeypatch):
    def handler(req):
        if req.url.host == "public.example":
            return httpx.Response(301, headers={"location": "https://www.public.example/final"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="landed")

    _client_with(monkeypatch, handler)
    got = F.fetch_with_provenance("http://public.example/x", polite=False)
    assert got["text"] == "landed" and got["provenance"]["final_url"] == "https://www.public.example/final"


def test_a_redirect_loop_stops(monkeypatch):
    _client_with(monkeypatch, lambda req: httpx.Response(302, headers={"location": "http://public.example/again"}))
    with pytest.raises(F.FetchError, match="redirect"):
        F.fetch_with_provenance("http://public.example/x", polite=False)
