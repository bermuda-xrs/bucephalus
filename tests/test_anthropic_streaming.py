from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from agent.config import BackendConfig
from agent.models.anthropic import AnthropicBackend
from agent.models.base import BackendError


class FakeStream:
    def __init__(self, chunks, final):
        self._chunks, self._final = chunks, final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        return iter(self._chunks)

    def get_final_message(self):
        return self._final


class FakeMessages:
    def __init__(self, stream):
        self._stream = stream
        self.kwargs = None

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return self._stream

    def create(self, **kwargs):
        raise AssertionError("the non-streaming path must not be used")


def final(text="hello world", stop="end_turn"):
    return NS(
        content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
        stop_reason=stop, stop_details=None, model="claude-opus-5",
        usage=NS(input_tokens=120, output_tokens=5,
                 cache_read_input_tokens=100, cache_creation_input_tokens=0),
    )


def backend(chunks, msg):
    client = NS(messages=FakeMessages(FakeStream(chunks, msg)))
    cfg = BackendConfig(kind="anthropic", model="claude-opus-5")
    return AnthropicBackend("cloud", cfg, client=client), client


def test_text_reaches_the_surface_while_the_turn_is_running():
    b, _ = backend(["hel", "lo ", "world"], final())
    got = []
    resp = b.complete("PREFIX", "tail", on_chunk=got.append)
    assert got == ["hel", "lo ", "world"], "chunks in order, as they arrived"
    assert resp.text == "hello world", "thinking blocks are not the answer"
    assert resp.usage.cache_read_tokens == 100


def test_there_is_one_path_even_with_no_listener():
    b, client = backend(["ok"], final("ok"))
    assert b.complete("PREFIX", "tail").text == "ok"
    assert client.messages.kwargs is not None


def test_the_request_shape_is_unchanged_by_streaming():
    b, client = backend(["ok"], final("ok"))
    b.complete("PREFIX", "tail")
    kw = client.messages.kwargs
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["messages"] == [{"role": "user", "content": "tail"}]
    assert "stream" not in kw, "the helper streams; the flag is not ours to pass"
    assert kw["output_config"]["effort"] == b.cfg.effort


def test_a_refusal_is_still_reported_after_streaming():
    b, _ = backend([], final("", stop="refusal"))
    with pytest.raises(BackendError):
        b.complete("PREFIX", "tail", on_chunk=lambda t: None)


def test_a_native_tool_call_becomes_the_action_the_loop_reads():
    import json

    msg = NS(content=[NS(type="text", text="I'll read it."),
                      NS(type="tool_use", id="t1", name="read_file", input={"path": "work/a.txt"})],
             stop_reason="tool_use", stop_details=None, model="claude-opus-5",
             usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    b, _ = backend(["I'll read it."], msg)
    got = json.loads(b.complete("P", "t", tools=[{"name": "read_file", "input_schema": {}}]).text)
    assert got == {"kind": "action", "tool": "read_file", "args": {"path": "work/a.txt"}}
