from __future__ import annotations

from ..config import BackendConfig, Config
from .anthropic import AnthropicBackend
from .base import Backend
from .mock import MockBackend
from .ollama import OllamaBackend
from .openai_compat import OpenAICompatibleBackend


def build_backend(name: str, cfg: BackendConfig) -> Backend:
    if cfg.kind == "anthropic":
        return AnthropicBackend(name, cfg)  # type: ignore[return-value]
    if cfg.kind == "ollama":
        return OllamaBackend(name, cfg)  # type: ignore[return-value]
    if cfg.kind == "openai":
        return OpenAICompatibleBackend(name, cfg)  # type: ignore[return-value]
    if cfg.kind == "mock":
        return MockBackend(name, cfg)  # type: ignore[return-value]
    raise ValueError(f"unknown backend kind {cfg.kind!r}")


def build_backends(config: Config) -> dict[str, Backend]:
    return {name: build_backend(name, cfg) for name, cfg in config.backends.items()}


def cloud_backend_names(config: Config) -> set[str]:
    return {n for n, c in config.backends.items() if c.trust_tier == "cloud"}


def cache_reporting_backend_names(config: Config) -> set[str]:
    return {n for n, c in config.backends.items() if c.reports_prompt_cache}
