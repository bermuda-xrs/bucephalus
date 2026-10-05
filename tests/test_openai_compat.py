from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.config import BackendConfig  # noqa: E402
from agent.models import stall as stall_mod  # noqa: E402
from agent.models.base import BackendError  # noqa: E402
from agent.models.openai_compat import (  # noqa: E402
    OpenAICompatibleBackend,
    to_openai_tools,
)
from agent.models.registry import build_backend  # noqa: E402
from agent.profiles import ProfileViolation, enforce, reaches_a_remote_provider  # noqa: E402


def sse(*chunks: str) -> list[str]:
    out: list[str] = []
    for c in chunks:
        out.extend([f"data: {c}", ""])
    out.extend(["data: [DONE]", ""])
    return out


class FakeStream:
    def __init__(self, lines, status=200, body=""):
        self._lines, self.status_code, self._body = lines, status, body
        self.text = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        yield from self._lines

    def read(self):
        return self._body.encode()


class FakeClient:
    def __init__(self, lines, status=200, body=""):
        self.lines, self.status, self.body = lines, status, body
        self.sent: list[dict] = []
        self.headers: list[dict] = []

    def stream(self, method, url, **kw):
        self.sent.append({"method": method, "url": url, "json": kw.get("json")})
        self.headers.append(kw.get("headers") or {})
        return FakeStream(self.lines, self.status, self.body)


TEXT_STREAM = sse(
    '{"choices":[{"delta":{"content":"Jup"}}]}',
    '{"choices":[{"delta":{"content":"iter"}}]}',
    '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
    '{"choices":[],"usage":{"prompt_tokens":32,"completion_tokens":12,'
    '"prompt_tokens_details":{"cached_tokens":31}}}',
)


def cfg(**kw) -> BackendConfig:
    base = dict(kind="openai", model="granite4.1:3b",
                base_url="http://localhost:11435/v1", trust_tier="local")
    base.update(kw)
    return BackendConfig(**base)


def backend(lines=TEXT_STREAM, **kw):
    c = FakeClient(lines, **kw)
    return OpenAICompatibleBackend("t", cfg(), client=c), c


def test_a_streamed_reply_is_reassembled_with_its_usage():
    b, _ = backend()
    r = b.complete("system", "user", None, 64, 0.0)
    assert r.text == "Jupiter"
    assert r.usage.input_tokens == 32
    assert r.usage.output_tokens == 12
    assert r.usage.cache_read_tokens == 31


def test_token_counts_come_from_the_server_not_from_counted_chunks():
    b, _ = backend()
    assert b.complete("s", "u", None, 64, 0.0).usage.output_tokens == 12


def test_without_a_usage_block_the_count_falls_back_to_chunks_not_to_zero():
    b, _ = backend(sse('{"choices":[{"delta":{"content":"hi"}}]}',
                       '{"choices":[{"delta":{},"finish_reason":"stop"}]}'))
    r = b.complete("s", "u", None, 64, 0.0)
    assert r.usage.output_tokens == 1
    assert r.raw["usage_reported"] is False


def test_a_json_schema_is_sent_as_response_format():
    b, c = backend()
    schema = {"type": "object", "properties": {"planet": {"type": "string"}}}
    b.complete("s", "u", None, 64, 0.0, schema)
    sent = c.sent[0]["json"]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"] == schema


def test_tool_schemas_are_converted_to_the_openai_function_shape():
    got = to_openai_tools([{"name": "read_file", "description": "read",
                            "input_schema": {"type": "object"}}])
    assert got == [{"type": "function",
                    "function": {"name": "read_file", "description": "read",
                                 "parameters": {"type": "object"}}}]
    already = [{"type": "function", "function": {"name": "x"}}]
    assert to_openai_tools(already) == already, "already-converted passes through"


def test_temperature_is_omitted_when_the_model_rejects_it():
    c = FakeClient(TEXT_STREAM)
    b = OpenAICompatibleBackend("t", cfg(accepts_temperature=False), client=c)
    b.complete("s", "u", None, 64, 0.7)
    assert "temperature" not in c.sent[0]["json"]


def test_the_max_tokens_field_is_declared_not_guessed():
    c = FakeClient(TEXT_STREAM)
    b = OpenAICompatibleBackend(
        "t", cfg(max_tokens_field="max_completion_tokens"), client=c)
    b.complete("s", "u", None, 64, 0.0)
    assert "max_completion_tokens" in c.sent[0]["json"]
    assert "max_tokens" not in c.sent[0]["json"]


def test_an_http_error_carries_the_servers_own_message():
    b, _ = backend(TEXT_STREAM, status=400,
                   body='{"error":{"message":"response_format unsupported"}}')
    with pytest.raises(BackendError, match="response_format unsupported"):
        b.complete("s", "u", None, 64, 0.0)


def test_a_declared_key_env_that_is_unset_refuses_rather_than_calling_anonymously():
    c = FakeClient(TEXT_STREAM)
    b = OpenAICompatibleBackend("t", cfg(api_key_env="NOT_SET_ANYWHERE"),
                                client=c)
    with pytest.raises(BackendError, match="NOT_SET_ANYWHERE"):
        b.complete("s", "u", None, 64, 0.0)
    assert not c.sent, "nothing may leave the machine on a missing credential"


def test_the_key_is_read_from_the_environment_never_from_config(monkeypatch):
    monkeypatch.setenv("SOME_KEY", "sk-secret")
    c = FakeClient(TEXT_STREAM)
    b = OpenAICompatibleBackend("t", cfg(api_key_env="SOME_KEY"), client=c)
    b.complete("s", "u", None, 64, 0.0)
    assert c.headers[0]["Authorization"] == "Bearer sk-secret"
    assert "sk-secret" not in str(cfg(api_key_env="SOME_KEY").model_dump()), (
        "a key in a config object is a key in a config file is a key in git")


def test_a_backend_with_no_url_fails_at_construction_not_at_first_call():
    with pytest.raises(ValueError, match="base_url"):
        build_backend("t", BackendConfig(kind="openai", model="m"))


class _Cfg:
    def __init__(self, backends):
        self.backends = backends


def test_profile_local_refuses_a_cloud_backend():
    c = _Cfg({"cloud": BackendConfig(kind="anthropic", model="claude-opus-5",
                                     trust_tier="cloud")})
    with pytest.raises(ProfileViolation, match="cloud backends present"):
        enforce(c, "local", env={})


def test_profile_local_refuses_a_remote_openai_endpoint_labelled_local():
    c = _Cfg({"exec": BackendConfig(kind="openai", model="gpt-4o",
                                    base_url="https://api.openai.com/v1",
                                    trust_tier="local")})
    assert reaches_a_remote_provider(c.backends["exec"]) is True
    with pytest.raises(ProfileViolation, match="cloud backends present"):
        enforce(c, "local", env={})


def test_a_genuinely_local_openai_endpoint_is_allowed_under_profile_local():
    c = _Cfg({"exec": cfg()})
    assert reaches_a_remote_provider(c.backends["exec"]) is False
    prov = enforce(c, "local", env={})
    assert prov["cloud_backends"] == []


@pytest.mark.parametrize("url,remote", [
    ("http://localhost:11435/v1", False),
    ("http://127.0.0.1:8000/v1", False),
    ("http://192.168.1.50:8000/v1", False),
    ("https://api.openai.com/v1", True),
    ("https://oai.example.invalid/v1", True),
])
def test_locality_is_decided_by_the_host(url, remote):
    assert reaches_a_remote_provider(cfg(base_url=url)) is remote


def test_a_key_env_named_by_a_backend_is_watched_even_if_unlisted(monkeypatch):
    c = _Cfg({"exec": cfg(api_key_env="SOME_NEW_PROVIDER_KEY")})
    with pytest.raises(ProfileViolation, match="SOME_NEW_PROVIDER_KEY"):
        enforce(c, "local", env={"SOME_NEW_PROVIDER_KEY": "sk-x"})


def test_the_action_envelope_follows_the_tier_not_the_adapter():
    assert cfg(kind="ollama", endpoint="http://localhost:11435").needs_action_envelope
    assert cfg(kind="openai").needs_action_envelope
    assert not cfg(kind="anthropic", trust_tier="cloud",
                   base_url=None).needs_action_envelope


def test_the_stall_switch_is_shared_so_an_injected_index_survives_the_switch():
    stall_mod.reset_injection_counter()
    b1, _ = backend()
    b2 = build_backend("o", BackendConfig(kind="ollama", model="m",
                                          endpoint="http://localhost:11435",
                                          trust_tier="local"))
    b1.complete("s", "u", None, 8, 0.0)
    assert stall_mod._CALLS["n"] == 1  # noqa: SLF001
    assert b2.cfg.kind == "ollama"
    stall_mod.reset_injection_counter()


def test_an_injected_stall_on_the_new_adapter_is_a_model_stall(monkeypatch):
    monkeypatch.setenv("ALPHA_INJECT_STALL", "1")
    stall_mod.reset_injection_counter()
    b, c = backend()
    with pytest.raises(stall_mod.ModelStall, match="INJECTED"):
        b.complete("s", "u", None, 8, 0.0)
    assert not c.sent, "an injected stall must not reach the server"
    stall_mod.reset_injection_counter()


def test_the_configs_differ_only_where_they_are_meant_to():
    import yaml
    a = yaml.safe_load((ROOT / "config.local.yaml").read_text(encoding="utf-8"))
    b = yaml.safe_load((ROOT / "config.openai.yaml").read_text(encoding="utf-8"))
    assert set(a["backends"]) == set(b["backends"])
    for name in a["backends"]:
        ba, bb = dict(a["backends"][name]), dict(b["backends"][name])
        assert ba.pop("kind") == "ollama" and bb.pop("kind") == "openai"
        assert bb.pop("base_url") == ba.pop("endpoint") + "/v1"
        assert bb.pop("reports_prompt_cache") is True
        assert "reports_prompt_cache" not in ba
        assert ba == bb, f"backend {name!r} differs beyond the adapter swap"
    for key in ("limits", "gate", "autonomy", "active_local_tier",
                "compress_observations_over_tokens"):
        assert a[key] == b[key], f"{key} differs"
