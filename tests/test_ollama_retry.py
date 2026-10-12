from __future__ import annotations

import json

import httpx
import pytest

from agent.config import BackendConfig
from agent.models.base import BackendError
from agent.models.ollama import OllamaBackend

DONE = [json.dumps({"message": {"content": "hel"}}), json.dumps({"message": {"content": "lo"}}),
        json.dumps({"done": True, "eval_count": 2, "prompt_eval_count": 5})]


class Stream:
    def __init__(self, lines, fail_after=None):
        self.lines, self.fail_after = lines, fail_after

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self):
        for i, line in enumerate(self.lines):
            if self.fail_after is not None and i == self.fail_after:
                raise httpx.ReadError("[WinError 10054] An existing connection was forcibly closed by the remote host")
            yield line


class Client:
    def __init__(self, plan):
        self.plan, self.calls = list(plan), 0

    def stream(self, method, url, **kw):
        self.calls += 1
        step = self.plan.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _b(client):
    return OllamaBackend("t", BackendConfig(kind="ollama", model="m", endpoint="http://localhost:11435"), client=client)


def test_a_connection_dropped_before_any_token_is_tried_once_more(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = Client([httpx.ConnectError("refused"), Stream(DONE)])
    r = _b(c).complete("p", "t")
    assert r.text == "hello" and c.calls == 2


def test_a_reset_before_the_first_token_is_retried_too(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = Client([Stream(DONE, fail_after=0), Stream(DONE)])
    assert _b(c).complete("p", "t").text == "hello" and c.calls == 2


def test_a_drop_after_tokens_streamed_is_not_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = Client([Stream(DONE, fail_after=1), Stream(DONE)])
    with pytest.raises(BackendError):
        _b(c).complete("p", "t", on_chunk=lambda s: None)
    assert c.calls == 1


def test_it_is_tried_once_only(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = Client([httpx.ConnectError("x"), httpx.ConnectError("y"), Stream(DONE)])
    with pytest.raises(BackendError):
        _b(c).complete("p", "t")
    assert c.calls == 2


def test_an_http_error_status_is_not_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    class Bad(Stream):
        def raise_for_status(self):
            raise httpx.HTTPStatusError("500", request=httpx.Request("POST", "http://x"), response=httpx.Response(500))

    c = Client([Bad(DONE), Stream(DONE)])
    with pytest.raises(BackendError):
        _b(c).complete("p", "t")
    assert c.calls == 1
