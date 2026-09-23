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
    # A5. How many non-model tool calls `parallel` may run at once. 1 is
    # sequential, which is what a measurement run gets: wall clock IS this
    # project's cost axis, so a task that read five files at once is not
    # comparable with one that read them in turn. Recorded in the run record
    # either way — `bench/measurement.py` sets it and reports it.
    #
    # MODEL calls are never parallelised at any value. One Ollama instance on
    # CPU: concurrent generation contends for the same cores and corrupts the
    # only cost axis this project has.
    max_parallel_tools: int = 4


class GateConfig(BaseModel):
    min_paired_trials: int = 30
    min_lift_pp: float = 5.0
    max_p_value: float = 0.05
    min_discordant_pairs: int = 10


class MechanismsConfig(BaseModel):
    """G1's ablation switches. Every one defaults to ON: the shipped system.

    G1 claims a >=10x reduction in paid tokens, which is a comparison against
    the same work done WITHOUT the mechanisms - and until now there was
    nothing to compare against. The four were structural in `execute/loop.py`
    with no switches, so no configuration could express "without compaction",
    and RELEASE's G1 section named that, not the API key, as the blocker
    nobody had costed.

    Each flag removes the MECHANISM, never merely its enforcement. That
    distinction is the one this project keeps getting burned by: a control
    turned off beside a mechanism that still runs is a decoy branch (DR2), and
    the arm would silently measure nothing at all.

      * `compaction`       - off: the ceiling still reports, nothing is
                             tombstoned, on EITHER route (the loop's session
                             boundary and the refused-delegation fallback).
                             Both, or the second keeps compacting while the
                             record says the arm did not - DR3.
      * `semantic_cache`   - off: every compression re-summarizes. The cache
                             is not merely bypassed on read; nothing is
                             written either, so the arm cannot warm a store a
                             later arm would read.
      * `prefix_stability` - off: skills and tier-1 memory are re-read LIVE
                             each step instead of snapshotted at task start,
                             and the drift guard is not armed. Disarming the
                             guard alone would change no bytes and measure
                             nothing - the guard detects stability, the
                             snapshots create it.
      * `code_as_action`   - off: `run_code` is denied to every task AND
                             removed from the tool schemas, the tool manual
                             and the system contract. A model still told to
                             emit `<code>` and then refused would measure a
                             model fighting a denial, which is adjacent to
                             the question and not it (C3's largest class).

    Turning any of these off changes the prompt bytes, so an ablation arm is
    NOT comparable with a shipped-system number as a prefix-cache measurement.
    It is comparable as what it is: the same tasks, the same model, one
    mechanism absent. The settings land in `profile_provenance`, so every run
    record says which arm produced it and no chart can blend them.
    """

    compaction: bool = True
    semantic_cache: bool = True
    prefix_stability: bool = True
    code_as_action: bool = True

    def ablated(self) -> list[str]:
        """The mechanisms that are OFF. Empty means the shipped system."""
        return sorted(k for k, v in self.model_dump().items() if not v)


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


class TelegramConfig(BaseModel):
    """A4. Off by default, and fail-closed when on.

    `allowed_senders` EMPTY MEANS NOBODY. Not everybody. The other reading is
    the classic way an allowlist becomes decoration: someone turns the channel
    on before filling in the list, and an empty-means-open default hands the
    bot to whoever finds it first. There is a test for this specific value.

    The bot token is NOT here and never will be. It lives in an environment
    variable, because a config file is the thing people paste into an issue.
    """

    enabled: bool = False
    allowed_senders: list[str] = Field(default_factory=list)
    token_env: str = "ALPHA_TELEGRAM_TOKEN"
    # Long poll, not a webhook: a webhook needs a public HTTPS endpoint, which
    # means binding a non-loopback interface and putting this project's
    # control plane on the internet. Polling adds no listening surface at all.
    poll_timeout_s: int = 25
    artifact_scope: list[str] = Field(default_factory=lambda: ["work"])


class ChannelConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)


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
    # G1's ablation arms. Defaults are the shipped system; see MechanismsConfig.
    mechanisms: MechanismsConfig = Field(default_factory=MechanismsConfig)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    channels: ChannelConfig = Field(default_factory=ChannelConfig)
    # Retrieval isolation for measurement runs. See kernel/scope.py.
    #
    # NOT normally set from yaml: `bench/noise.py` turns it on itself, because
    # a paired trial contaminated by someone editing the wrong config file is
    # precisely the failure it exists to remove. It lives here so every
    # persistence subsystem reads one flag rather than each inventing its own.
    # None means UNDECLARED, and that is not the same as False. False is
    # "considered and not needed"; None is "nobody thought about it". A
    # measurement path must not be able to produce the second by accident -
    # see bench/measurement.py.
    isolate_retrieval: bool | None = None
    # SYNTHETIC COMPACTION BOUNDARY, for measuring what compaction costs.
    #
    # Set to a step number and the loop treats the ceiling as crossed at that
    # step, whatever the real fill is. Off (None) everywhere except a
    # measurement that says so.
    #
    # Why this exists, stated here because a synthetic trigger that nobody can
    # see in the record is a lie: the ceiling is 0.28 * 16384 = 4587 tokens,
    # and on granite4.1:3b it is essentially never reached. Four pilot designs
    # failed to trigger it - the model answers from nothing rather than
    # reading (FINDINGS 1.32), and when it does act it reaches for `run_code`,
    # which reads the file INSIDE the sandbox so the bytes never enter the
    # context at all. Waiting for a real crossing measures the model's
    # reluctance, not the mechanism.
    #
    # So the condition is injected instead, which is this project's own rule:
    # prefer injecting a condition over waiting to observe it. What that buys
    # is a clean read of what compaction costs WHEN IT FIRES. What it does not
    # buy is how often it fires, and the two must be reported together or the
    # number misleads.
    #
    # `profile_provenance["compaction_boundary"]` says "synthetic, step N" in
    # every record produced this way.
    compaction_probe_step: int | None = None
    # THE GRADER IS HELD OUT UNDER MEASUREMENT. Set by `open_measurement`,
    # never from yaml, and deliberately NOT keyed on `isolate_retrieval`: the
    # prefix-stability arm runs un-isolated on purpose, and must hold the
    # grader out all the same.
    #
    # One verifier was playing two roles - GRADING the task and FEEDING
    # REFINEMENT - and under measurement the second role handed over the
    # first one's answer key: `expected_value` said "$.total: expected 4825,
    # got 0", `contains_all` said "missing: 'INF-4471', ...", and the model
    # copied them into its next turn and passed (FINDINGS 1.33). Held out,
    # the grading verifier's result reaches the model as PASS or FAIL only -
    # no score, no location, no text - and its full detail goes to the
    # `verify.failed` event for the operator.
    #
    # Outside measurement nothing changes: a user's own tests saying
    # "expected 9, got 7" is exactly what the refinement loop exists to
    # deliver. It is a leak only when the thing supplying it is also the
    # thing keeping score.
    grader_held_out: bool = False
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
    # Which G1 arm this is, on the channel that already reaches every run
    # record (instrument/report.py, bench/noise.py, bench/run.py). A number
    # produced with a mechanism ablated must not be able to arrive anywhere
    # labelled the same as a number produced without - the same argument that
    # makes the PROFILE declared and enforced rather than whatever config
    # happened to be on disk.
    cfg.profile_provenance["mechanisms"] = cfg.mechanisms.model_dump()
    cfg.profile_provenance["ablated"] = cfg.mechanisms.ablated()
    cfg.profile_provenance["compaction_boundary"] = (
        f"SYNTHETIC: forced at step {cfg.compaction_probe_step}, whatever the "
        "real context fill. Measures what compaction costs when it fires, and "
        "says nothing about how often it fires - see FINDINGS 1.32"
        if cfg.compaction_probe_step is not None else "real: the fill ratio")
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
