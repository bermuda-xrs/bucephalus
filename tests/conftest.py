from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from agent.config import AutonomyConfig, BackendConfig, Config, GateConfig, Limits
from agent.context import AgentContext

_SCRATCH = tempfile.mkdtemp(prefix="bucephalus_user_")
atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
for _var, _name in (("BUCEPHALUS_DATA_DIR", "data"), ("BUCEPHALUS_CONFIG_DIR", "config")):
    os.environ[_var] = os.path.join(_SCRATCH, _name)
    os.makedirs(os.environ[_var])


@pytest.fixture(autouse=True)
def _user_dirs_are_scratch(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("BUCEPHALUS_CONFIG_DIR", str(tmp_path_factory.mktemp("user_config")))
    data = str(tmp_path_factory.mktemp("user_data"))
    monkeypatch.setenv("BUCEPHALUS_DATA_DIR", data)
    monkeypatch.setattr("platformdirs.user_data_dir", lambda *a, **k: data)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_on_free_port(launch, probe, attempts: int = 4, timeout: float = 60.0):
    for _ in range(attempts):
        port = free_port()
        p = launch(port)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if p.poll() is not None:
                break
            if probe(port):
                return p, port
            time.sleep(0.3)
        else:
            _reap(p)
            raise TimeoutError(f"the server on {port} never answered")
        _reap(p)
    raise RuntimeError(f"the server did not start in {attempts} attempts (a port taken between choosing and binding it, or a real failure)")


def _reap(p) -> None:
    from agent.proc import kill_tree

    if p.poll() is None:
        kill_tree(p.pid)
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()


def make_config(tmp_path: Path, **overrides) -> Config:
    cfg = Config(
        db_path=str(tmp_path / "agent.db"),
        anchor_path=str(tmp_path / "anchor" / "chain.anchor"),
        skills_path=str(tmp_path / "skills_store"),
        root=str(tmp_path),
        backends={
            "cloud": BackendConfig(
                kind="mock",
                reports_prompt_cache=True,
                model="mock-cloud",
                input_cost_per_mtok=5.0,
                output_cost_per_mtok=25.0,
                cache_read_per_mtok=0.5,
                cache_write_per_mtok=6.25,
                context_window=200000,
                usable_context_window=200000,
                trust_tier="cloud",
                max_output_tokens=4096,
            ),
            "review": BackendConfig(
                kind="mock",
                reports_prompt_cache=True,
                model="mock-review",
                input_cost_per_mtok=3.0,
                output_cost_per_mtok=15.0,
                trust_tier="cloud",
                max_output_tokens=1024,
            ),
            "local": BackendConfig(
                kind="mock",
                reports_prompt_cache=True,
                model="mock-local",
                trust_tier="local",
                context_window=120000,
                usable_context_window=120000,
                max_output_tokens=2048,
            ),
            "laptop": BackendConfig(
                kind="mock",
                reports_prompt_cache=True,
                model="mock-laptop",
                trust_tier="local",
                context_window=32768,
                usable_context_window=32768,
                thinking="disabled",
                max_output_tokens=1024,
            ),
        },
        active_local_tier="local",
        limits=Limits(max_steps=6, default_task_budget_usd=1.0),
        local_allowlist=["summarize", "classify", "extract", "compress_tool_output",
                         "draft_candidate"],
        laptop_allowlist=["classify", "extract", "compress_tool_output"],
        compress_observations_over_tokens=100000,
        gate=GateConfig(min_paired_trials=30, min_lift_pp=5.0, max_p_value=0.05,
                        min_discordant_pairs=10),
        autonomy=AutonomyConfig(
            plan_required=False,
            auto_review=True,
            auto_review_backend="review",
            auto_review_policy=str(tmp_path / "policy.md"),
        ),
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "work"
    ws.mkdir()
    (tmp_path / "policy.md").write_text(
        "# policy\n\nDefault is DENY.\n\n"
        "## Approve when all of these hold\n"
        "- tool is write_file or run_code\n- paths inside artifact_scope\n"
        "- taint is trusted or user\n\n"
        "## Always deny\n- credentials anywhere\n- writes outside artifact_scope\n",
        encoding="utf-8",
    )
    return ws


@pytest.fixture
def ctx(tmp_path: Path, workspace: Path):
    c = AgentContext(make_config(tmp_path))
    yield c
    c.close()


@pytest.fixture
def task(ctx, workspace):
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    return create_task(
        ctx.conn,
        ctx.log,
        TaskContract(goal="test goal", artifact_scope=["work"]),
        Budget(usd=1.0, seconds=600),
    )


@pytest.fixture
def scripted(ctx):
    def _q(backend: str, *texts: str):
        ctx.backends[backend].queue(*texts)

    return _q


@pytest.fixture
def python_exe() -> str:
    return sys.executable
