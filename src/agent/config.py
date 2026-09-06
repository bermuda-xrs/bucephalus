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
    kind: Literal["anthropic", "ollama", "openai", "mock"]
    model: str
    endpoint: str | None = None
    # `openai` only. The OpenAI chat-completions protocol, spoken to whatever
    # is listening: Ollama's /v1 shim, vLLM, llama.cpp server, or a hosted
    # endpoint. `base_url` is the field the protocol names; `endpoint` is
    # accepted as an alias so an existing entry can change kind without being
    # rewritten. See `resolved_base_url`.
    base_url: str | None = None
    # Name of the environment variable holding the key, NEVER the key itself.
    # A key in a config file is a key in git history.
    api_key_env: str | None = None
    api_mode: Literal["chat_completions"] = "chat_completions"
    # Newer OpenAI models reject `max_tokens` and require
    # `max_completion_tokens`; Ollama, vLLM and llama.cpp take `max_tokens`.
    # Declared rather than sniffed — a wrong value here is a 400 at first use,
    # which is loud, where a silent fallback would quietly change what was
    # asked for.
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    # Does this backend report prompt-cache accounting at all?
    #
    # A declared capability, because the distinction it carries is one this
    # project has already been burned by: `cache_hit_ratio` of 0.0 means
    # "nothing was cached" on a backend that reports the number, and
    # "unmeasured" on one that does not. Averaging those together, or firing
    # an alarm on the second, is the same error as counting a machine fault
    # as a task failure.
    #
    # Ollama's native /api/chat reports nothing; its /v1 shim reports
    # `prompt_tokens_details.cached_tokens`; Anthropic reports
    # `cache_read_input_tokens`. MEASURED 2026-09-06 for the first two —
    # docs/evidence/phase1-openai-parity.json.
    reports_prompt_cache: bool = False
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
    # "json"  - constrained decoding. The envelope is guaranteed valid, but the
    #           model can close the object from inside the `code` string,
    #           costing ~1 step each time (the compile-check catches it).
    # "tags"  - <code>...</code>. No escaping, so no stray brace, but the model
    #           must follow a text format unaided.
    #
    # MEASURED - do not flip without re-measuring. granite4.1:3b reaches
    # <final> under BOTH. granite4.1:8b under "tags" produced 10 calls and 6
    # parseable actions across 8 tasks and NEVER a <final>, the same class of
    # failure as the 8B deputy that could not emit VERDICT at all. So "json"
    # is the safe default and "tags" is opt-in per backend.
    action_protocol: Literal["json", "tags"] = "json"
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

    @property
    def resolved_base_url(self) -> str:
        """Where an `openai`-kind backend actually sends its requests."""
        url = self.base_url or self.endpoint
        if not url:
            raise ValueError(
                f"backend kind={self.kind!r} model={self.model!r} needs a "
                "base_url (or endpoint) — there is no default for an "
                "OpenAI-compatible server"
            )
        return url.rstrip("/")

    @property
    def needs_action_envelope(self) -> bool:
        """Does this backend have to be handed a constrained action format?

        MEASURED, and a property rather than an adapter type. The check this
        replaces was `cfg.kind == "ollama"`, which was correct only for as
        long as there was exactly one local adapter. Point a second adapter at
        the same Ollama server running the same weights and the class-name
        test flips while nothing about the model changed — the run would
        silently be asking a different question. That is the same defect class
        as a benchmark whose profile is whatever config happened to be on
        disk, so it is settled the same way: by a declared property.

        Cloud tiers keep the tag protocol — they follow it reliably, and a
        forced schema fights with extended thinking.
        """
        return self.trust_tier == "local"

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
    # In a checkout this is the project directory, exactly as before. Once
    # installed there is no project directory, so state goes to platformdirs
    # rather than beside site-packages or wherever the caller happened to
    # stand. See agent/paths.py.
    from .paths import ensure_state_dirs, state_root

    raw["root"] = str(state_root(path))
    ensure_state_dirs(raw["root"])
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
