from __future__ import annotations

from dataclasses import dataclass

from ..config import BackendConfig, Config
from .anthropic import AnthropicBackend
from .base import Backend
from .mock import MockBackend
from .ollama import OllamaBackend
from .openai_compat import OpenAICompatibleBackend


@dataclass
class CostVector:
    input_cost_per_mtok: float
    output_cost_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float
    tokens_per_sec: float
    context_window: int
    can_parallel: bool
    trust_tier: str


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


def cost_vector(backend: Backend, probe: bool = False) -> CostVector:
    cfg = backend.cfg
    if probe:
        backend.probe_throughput()  # type: ignore[attr-defined]
    return CostVector(
        input_cost_per_mtok=cfg.input_cost_per_mtok,
        output_cost_per_mtok=cfg.output_cost_per_mtok,
        cache_read_per_mtok=cfg.cache_read_per_mtok,
        cache_write_per_mtok=cfg.cache_write_per_mtok,
        tokens_per_sec=backend.measure_throughput(),
        context_window=cfg.budgeted_window,
        can_parallel=cfg.can_parallel,
        trust_tier=cfg.trust_tier,
    )


def cloud_backend_names(config: Config) -> set[str]:
    return {n for n, c in config.backends.items() if c.trust_tier == "cloud"}


def cache_reporting_backend_names(config: Config) -> set[str]:
    return {n for n, c in config.backends.items() if c.reports_prompt_cache}
