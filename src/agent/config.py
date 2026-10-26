from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr, model_validator


SMALL_FILL_RATIO = 0.5
MECHANISMS = ("compact_prompt", "evidence_pins", "stuck_patterns", "escape_check",
            "code_out_of_json", "progress_file", "evaluator_pass", "send_tool_list", "edit_first", "compute_first")


class _Section(BaseModel):
    # a misspelled key would be dropped in silence; its NAME is kept to be reported, never its value (it may be a key)
    _unknown: list[str] = PrivateAttr(default_factory=list)

    @model_validator(mode="wrap")
    @classmethod
    def _note_unknown(cls, data, handler):
        unknown = [k for k in data if k not in cls.model_fields] if isinstance(data, dict) else []
        m = handler({k: v for k, v in data.items() if k not in unknown} if unknown else data)
        m._unknown = [str(k) for k in unknown]
        return m


def ignored_keys(m: BaseModel, path: str = "") -> list[str]:
    out = [path + k for k in getattr(m, "_unknown", [])]
    for name in type(m).model_fields:
        v = getattr(m, name)
        items = v.items() if isinstance(v, dict) else enumerate(v) if isinstance(v, list) else [(None, v)]
        for key, x in items:
            if isinstance(x, BaseModel):
                out += ignored_keys(x, f"{path}{name}." + ("" if key is None else f"{key}."))
    return out


class BackendConfig(_Section):
    kind: Literal["anthropic", "ollama", "openai", "mock"]
    model: str
    endpoint: str | None = None
    base_url: str | None = None
    model_server: Literal["ollama"] | None = None
    api_key_env: str | None = None
    api_mode: Literal["chat_completions"] = "chat_completions"
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
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
    action_protocol: Literal["json", "tags"] = "json"
    keep_alive: str = "30m"
    tool_profile: Literal["full", "patch", "small"] = "full"
    read_first: bool | None = None
    read_all_pages: bool | None = None
    reasoning_field: bool = False
    carry_field: bool | None = None
    code_out_of_json: bool = False
    tool_call_mode: Literal["constrained", "native"] = "constrained"
    tool_names: Literal["internal", "familiar"] = "internal"
    compact_prompt: bool | None = None
    evidence_pins: bool | None = None
    progress_file: bool = False
    context_fill_ratio: float | None = None
    stuck_patterns: bool | None = None
    evaluator_pass: bool = False
    edit_format: Literal["default", "hashline"] | None = None
    send_tool_list: bool = False
    escape_check: bool | None = None
    edit_first: bool = False
    compute_first: bool = False
    observation_tokens: int | None = None
    assumed_tokens_per_sec: float = 5.0

    @property
    def budgeted_window(self) -> int:
        return self.usable_context_window or self.context_window

    @property
    def resolved_base_url(self) -> str:
        url = self.base_url or self.endpoint
        if not url:
            raise ValueError(
                f"backend kind={self.kind!r} model={self.model!r} needs a "
                "base_url (or endpoint) — there is no default for an "
                "OpenAI-compatible server"
            )
        return url.rstrip("/")

    @property
    def inline_observation_tokens(self) -> int:
        if self.observation_tokens is not None:
            return self.observation_tokens
        return int(self.budgeted_window * 0.4) if self.tool_profile == "small" else 400

    @property
    def requires_observation(self) -> bool:
        return self.tool_profile == "small" if self.read_first is None else self.read_first

    def on(self, name: str) -> bool:
        v = getattr(self, name)
        return self.tool_profile == "small" if v is None else bool(v)

    @property
    def edit_style(self) -> str:
        return self.edit_format or ("hashline" if self.tool_profile == "small" else "default")

    @property
    def fill_ratio(self) -> float | None:
        if self.context_fill_ratio is not None:
            return self.context_fill_ratio
        return SMALL_FILL_RATIO if self.tool_profile == "small" else None

    def resolved(self) -> dict:
        return {**{n: self.on(n) for n in MECHANISMS}, "reasoning_field": self.requires_thought,
                "carry_field": self.requires_carry, "edit_format": self.edit_style, "context_fill_ratio": self.fill_ratio}

    @property
    def requires_thought(self) -> bool:
        return self.reasoning_field

    @property
    def requires_carry(self) -> bool:
        return self.tool_profile == "small" if self.carry_field is None else self.carry_field

    @property
    def requires_all_pages(self) -> bool:
        return self.tool_profile == "small" if self.read_all_pages is None else self.read_all_pages

    @property
    def needs_action_envelope(self) -> bool:
        return self.trust_tier == "local"

    def cost_vector(self) -> dict[str, float]:
        return {
            "input_cost_per_mtok": self.input_cost_per_mtok,
            "output_cost_per_mtok": self.output_cost_per_mtok,
            "cache_read_per_mtok": self.cache_read_per_mtok,
            "cache_write_per_mtok": self.cache_write_per_mtok,
        }


class Limits(_Section):
    context_fill_ratio: float = 0.28
    default_task_budget_usd: float = 0.50
    default_task_budget_seconds: int = 600
    max_refinement_iterations: int = 6
    max_steps: int = 24
    delegation_min_isolated_tokens: int = 1000
    max_parallel_tools: int = 4


class GateConfig(_Section):
    min_paired_trials: int = 30
    min_lift_pp: float = 5.0
    max_p_value: float = 0.05
    min_discordant_pairs: int = 10


class HookConfig(_Section):
    argv: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    timeout_s: int = 10


class HooksConfig(_Section):
    pre_tool: list[HookConfig] = Field(default_factory=list)
    post_tool: list[HookConfig] = Field(default_factory=list)
    stop: list[HookConfig] = Field(default_factory=list)


class McpServerConfig(_Section):
    argv: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    read_only: list[str] = Field(default_factory=list)
    strict_paths: bool | None = None
    path_args: dict[str, list[str]] = Field(default_factory=dict)
    timeout_s: int = 30
    start_timeout_s: int = 20


class MechanismsConfig(_Section):
    compaction: bool = True
    semantic_cache: bool = True
    prefix_stability: bool = True
    code_as_action: bool = True
    diagnostics: bool = True
    verify_before_stop: bool = True
    constrained_calls: bool = True
    stuck_detection: bool = True
    shadow_checkpoints: bool = True
    agents_md: bool = True

    def ablated(self) -> list[str]:
        return sorted(k for k in type(self).model_fields if not getattr(self, k))


class AutonomyConfig(_Section):
    mode: Literal["plan_then_run", "interactive", "manual"] = "plan_then_run"
    plan_required: bool = True
    auto_review: bool = True
    auto_review_backend: str = "review"
    auto_review_policy: str = "./policy.md"
    deviation_tolerance: float = 0.25
    human_only_tiers: list[str] = Field(default_factory=lambda: ["trusted"])
    approve_scoped_writes: bool = False


class TelegramConfig(_Section):
    enabled: bool = False
    allowed_senders: list[str] = Field(default_factory=list)
    token_env: str = "BUCEPHALUS_TELEGRAM_TOKEN"
    poll_timeout_s: int = 25
    max_queue: int = 10
    artifact_scope: list[str] = Field(default_factory=lambda: ["work"])


class ChannelConfig(_Section):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)


class UIConfig(_Section):
    theme: str = "./themes/default.toml"
    banner_art: str = "./assets/banner.ans"
    background_image: str | None = None
    background_opacity: float = 0.15
    spinner: str = "braille"


class Config(_Section):
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
    mechanisms: MechanismsConfig = Field(default_factory=MechanismsConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    channels: ChannelConfig = Field(default_factory=ChannelConfig)
    isolate_retrieval: bool | None = None
    compaction_probe_step: int | None = None
    grader_held_out: bool = False
    root: str = "."
    state_dir: str = ""
    source_path: str = ""
    long_files: Literal["pages", "elide", "whole"] = "pages"
    git_mode: bool = False
    profile_provenance: dict[str, Any] = Field(default_factory=dict)

    def resolve(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (Path(self.root) / p).resolve()

    def resolve_state(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (Path(self.state_dir or self.root) / p).resolve()

    def validate_autonomy(self) -> None:
        name = self.autonomy.auto_review_backend
        if self.autonomy.auto_review and name not in self.backends:
            raise ValueError(
                f"autonomy.auto_review_backend={name!r} is not a configured backend"
            )


LONG_FILES = ("pages", "elide", "whole")
WHOLE_FILL_RATIO = 0.75


def fill_ratio(cfg, ctx=None) -> float:
    b = ctx.backends.get("cloud") if ctx is not None else None
    own = b.cfg.fill_ratio if b is not None else (cfg.backends["cloud"].fill_ratio
                                                         if "cloud" in cfg.backends else None)
    r = own if own is not None else cfg.limits.context_fill_ratio
    return max(r, WHOLE_FILL_RATIO) if cfg.long_files == "whole" else r


def load_config(path: str | Path = "config.yaml",
                profile: str | None = None) -> Config:
    path = Path(path)
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    from .paths import ensure_state_dirs, state_root

    raw["root"] = str(state_root(path))
    ensure_state_dirs(raw["root"])
    raw["state_dir"] = _synced_state_dir(raw)
    raw["source_path"] = str(path.resolve())
    return config_from_raw(raw, profile)


def config_from_raw(raw: dict, profile: str | None = None) -> Config:
    if profile is not None:
        raw["profile"] = profile
    cfg = Config.model_validate(raw)
    cfg.validate_autonomy()
    ignored = ignored_keys(cfg)
    if ignored:
        import sys

        print(f"bucephalus: these config keys are not settings and were ignored: {', '.join(ignored)}", file=sys.stderr)

    from .profiles import enforce

    cfg.profile_provenance = enforce(cfg, cfg.profile)
    cfg.profile_provenance["mechanisms"] = cfg.mechanisms.model_dump()
    cfg.profile_provenance["ablated"] = cfg.mechanisms.ablated()
    cfg.profile_provenance["long_files"] = cfg.long_files
    cfg.profile_provenance["ignored_keys"] = ignored
    cfg.profile_provenance["compaction_boundary"] = (
        f"SYNTHETIC: forced at step {cfg.compaction_probe_step}, whatever the "
        "real context fill. Measures what compaction costs when it fires, and "
        "says nothing about how often it fires - see FINDINGS 1.32"
        if cfg.compaction_probe_step is not None else "real: the fill ratio")
    return cfg


def _synced_state_dir(raw: dict) -> str:
    import sys

    from .paths import checkout_root, checkout_state_dir, sync_root

    root = Path(raw["root"])
    if os.environ.get("BUCEPHALUS_DATA_DIR") or checkout_root(root) != root.resolve():
        return ""
    synced = sync_root(root)
    if synced is None:
        return ""
    state = checkout_state_dir(root)
    if not state.exists():
        state.mkdir(parents=True)
        (state / "runs").mkdir()
        old = [str(root / raw[k]) for k in ("db_path", "anchor_path")
               if raw.get(k) and not Path(raw[k]).is_absolute() and (root / raw[k]).exists()]
        print(f"bucephalus: this checkout is inside a synced folder ({synced}), so its "
              f"database, anchor and run records now live in {state} and are not "
              "uploaded." + (" Existing state was NOT moved and is still synced: "
                             + ", ".join(old) + ". Move or delete it yourself."
                             if old else ""), file=sys.stderr)
    return str(state)


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
