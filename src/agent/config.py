"""Typed configuration. Loaded once, then read-only for the life of a task.

Config values must not change mid-task: the prefix builder hashes some of them
and a mid-task change would void the cache discount silently (M3.2).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class BackendConfig(BaseModel):
    kind: Literal["anthropic", "ollama", "mock"]
    model: str
    endpoint: str | None = None
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    cache_read_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0
    context_window: int = 200000
    usable_context_window: int | None = None
    can_parallel: bool = False
    trust_tier: Literal["cloud", "local"] = "cloud"
    speculative_decoding: bool = False
    thinking: Literal["adaptive", "disabled"] = "adaptive"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_output_tokens: int = 4096
    accepts_temperature: bool = True
    # Ollama only. How long to keep weights resident between calls. On CPU a
    # cold load costs ~30s against ~8s warm, so anything shorter than a task's
    # runtime pays that repeatedly for nothing.
    keep_alive: str = "30m"
    # What the router assumes until real work gives it something better. Only
    # used before any call to this backend has been recorded; measured rates
    # from the `calls` table win as soon as they exist.
    assumed_tokens_per_sec: float = 5.0

    @property
    def budgeted_window(self) -> int:
        """What M3.4 measures against — the usable window, not the advertised one."""
        return self.usable_context_window or self.context_window

    def cost_vector(self) -> dict[str, float]:
        return {
            "input_cost_per_mtok": self.input_cost_per_mtok,
            "output_cost_per_mtok": self.output_cost_per_mtok,
            "cache_read_per_mtok": self.cache_read_per_mtok,
            "cache_write_per_mtok": self.cache_write_per_mtok,
        }


class Limits(BaseModel):
    context_fill_ratio: float = 0.28
    default_task_budget_usd: float = 0.50
    default_task_budget_seconds: int = 600
    max_refinement_iterations: int = 6
    max_steps: int = 24
    delegation_min_isolated_tokens: int = 1000


class GateConfig(BaseModel):
    min_paired_trials: int = 30
    min_lift_pp: float = 5.0
    max_p_value: float = 0.05
    min_discordant_pairs: int = 10


class AutonomyConfig(BaseModel):
    mode: Literal["plan_then_run", "interactive", "manual"] = "plan_then_run"
    plan_required: bool = True
    auto_review: bool = True
    auto_review_backend: str = "review"
    auto_review_policy: str = "./policy.md"
    deviation_tolerance: float = 0.25
    human_only_tiers: list[str] = Field(default_factory=lambda: ["trusted"])
    # Treat the CODE gate as sufficient for a side-effecting action whose
    # arguments are inside artifact_scope and at or below USER taint — no
    # deputy, no human. Trusted-tier actions still park regardless.
    #
    # For benchmark runs, where the alternative is parking on every write or
    # putting a nondeterministic LLM in the measurement path. NOT a default:
    # unattended production work should keep a reviewer.
    approve_scoped_writes: bool = False


class UIConfig(BaseModel):
    theme: str = "./themes/default.toml"
    banner_art: str = "./assets/banner.ans"
    background_image: str | None = None
    background_opacity: float = 0.15
    spinner: str = "braille"


class Config(BaseModel):
    # What this run is allowed to touch. Enforced before any work starts, and
    # recorded on every artifact — see agent/profiles.py.
    profile: Literal["local", "mixed"] = "mixed"
    db_path: str = "./agent.db"
    anchor_path: str = "./anchor/chain.anchor"
    skills_path: str = "./skills_store"
    backends: dict[str, BackendConfig]
    active_local_tier: str | None = "local"
    limits: Limits = Field(default_factory=Limits)
    local_allowlist: list[str] = Field(default_factory=list)
    laptop_allowlist: list[str] = Field(default_factory=list)
    compress_observations_over_tokens: int = 1200
    gate: GateConfig = Field(default_factory=GateConfig)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    root: str = "."
    # Filled by load_config after enforcement; travels into every report.
    profile_provenance: dict[str, Any] = Field(default_factory=dict)

    def resolve(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (Path(self.root) / p).resolve()

    def validate_autonomy(self) -> None:
        """Invariant 14: the reviewer backend must exist and must not be the executor."""
        name = self.autonomy.auto_review_backend
        if self.autonomy.auto_review and name not in self.backends:
            raise ValueError(
                f"autonomy.auto_review_backend={name!r} is not a configured backend"
            )


def load_config(path: str | Path = "config.yaml",
                profile: str | None = None) -> Config:
    """Load and validate. `profile` overrides the file and is then ENFORCED.

    Enforcement raises rather than repairing: silently dropping a cloud
    backend would be its own lie about what ran.
    """
    path = Path(path)
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["root"] = str(path.parent.resolve())
    if profile is not None:
        raw["profile"] = profile
    cfg = Config.model_validate(raw)
    cfg.validate_autonomy()

    from .profiles import enforce

    cfg.profile_provenance = enforce(cfg, cfg.profile)
    return cfg


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env reader — no dependency, no surprises."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
