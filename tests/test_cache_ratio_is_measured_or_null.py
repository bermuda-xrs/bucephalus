"""The prefix observable, and why it reported nothing for the whole project.

`cache_hit_ratio` and `cache_alarm` have existed since M3.2. The alarm has
never fired once, and until 2026-09-06 it COULD not have, for two independent
reasons that each had to be fixed:

  1. No local backend reported cache accounting. Ollama's native /api/chat
     returns no cached-token field, so the ratio was a constant 0.0 — which is
     indistinguishable from a total prefix mutation, and is why the number was
     never trusted enough to act on. Phase 1 measured that the SAME SERVER
     reports it on /v1: an unseen 206-token prompt gave cached_tokens 5 then
     205 on the repeat, against 0 and 0 native.

  2. `cache_alarm` was gated on `paid_tokens > 0`. Under `--profile local`
     nothing costs money, so the gate was never true. The one machine this
     project runs on was the one machine the alarm could not protect.

Fixing either alone leaves it dead, which is why both are pinned here.

The distinction that carries the whole thing: **None is not zero.** None means
no backend on this task could report the number. 0.0 means a backend that CAN
report it said nothing was cached — which is the actual alarm condition.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.config import BackendConfig, Config  # noqa: E402
from agent.instrument.telemetry import Telemetry  # noqa: E402
from agent.kernel.db import open_db  # noqa: E402
from agent.kernel.types import TokenUsage  # noqa: E402
from agent.models.registry import cache_reporting_backend_names  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    c = open_db(tmp_path / "t.db")
    yield c
    c.close()


def record(conn: sqlite3.Connection, backend: str, task: str,
           input_tokens: int, cache_read: int) -> None:
    from agent.instrument.telemetry import record_call
    record_call(conn, task_id=task, backend=backend, purpose="step",
                usage=TokenUsage(input_tokens=input_tokens, output_tokens=8,
                                 cache_read_tokens=cache_read),
                cost=0.0, wall_ms=100)


def test_a_backend_that_cannot_report_caching_gives_none_not_zero(conn):
    """The native Ollama case, and every published run before 2026-09-06.

    Zero would read as "the prefix is being mutated on every call", which is
    a claim about the run. None is the truth: nobody measured.
    """
    t = Telemetry(conn, cloud_backends=set(), cache_reporting_backends=set())
    for _ in range(4):
        record(conn, "cloud", "t1", input_tokens=100, cache_read=0)
    m = t.metrics("t1")
    assert m.cache_hit_ratio is None
    assert m.as_dict()["cache_hit_ratio"] is None
    assert t.cache_alarm("t1") is None, (
        "an alarm must never fire on the ABSENCE of a measurement")


def test_a_reporting_backend_that_says_zero_does_raise_the_alarm(conn):
    """0.0 from a backend that CAN report is the real condition: the prefix
    is not being reused, which is what the alarm exists to catch."""
    t = Telemetry(conn, cloud_backends=set(),
                  cache_reporting_backends={"cloud"})
    for _ in range(4):
        record(conn, "cloud", "t2", input_tokens=100, cache_read=0)
    m = t.metrics("t2")
    assert m.cache_hit_ratio == 0.0
    assert "mutating the stable prefix" in (t.cache_alarm("t2") or "")


def test_the_alarm_is_no_longer_gated_on_spending_money(conn):
    """`paid_tokens > 0` made the alarm unreachable under --profile local.

    The spec justified this alarm by COST: a prefix mutation raises spend
    ~10x. On this hardware the same mutation is paid in wall clock instead,
    re-processing a prompt the server had already cached. Gating a
    correctness alarm on the axis that happens to be zero here is how it came
    to be dead on the only machine it runs on.
    """
    t = Telemetry(conn, cloud_backends=set(),          # nothing is paid
                  cache_reporting_backends={"cloud"})
    for _ in range(3):
        record(conn, "cloud", "t3", input_tokens=100, cache_read=0)
    assert t.metrics("t3").paid_tokens == 0
    assert t.cache_alarm("t3") is not None, (
        "a free backend can mutate its prefix too, and it costs wall clock, "
        "which is this project's binding budget")

    src = (ROOT / "src" / "agent" / "instrument" / "telemetry.py").read_text(
        encoding="utf-8")
    i = src.index("def cache_alarm")
    body = src[i:i + 2000]
    assert "paid_tokens" not in body.split('"""')[-1], (
        "the paid-tokens gate must not come back")


def test_a_healthy_prefix_does_not_alarm(conn):
    t = Telemetry(conn, cloud_backends=set(),
                  cache_reporting_backends={"cloud"})
    for _ in range(4):
        record(conn, "cloud", "t4", input_tokens=10, cache_read=90)
    assert t.metrics("t4").cache_hit_ratio == pytest.approx(0.9)
    assert t.cache_alarm("t4") is None


def test_calls_from_a_non_reporting_backend_do_not_drag_the_ratio_down(conn):
    """A task that used both a reporting and a non-reporting backend must not
    average a real 0.9 with an unmeasured 0.0 and call it 0.45. That would
    manufacture an alarm out of a protocol difference."""
    t = Telemetry(conn, cloud_backends=set(),
                  cache_reporting_backends={"reporting"})
    for _ in range(3):
        record(conn, "reporting", "t5", input_tokens=10, cache_read=90)
    for _ in range(3):
        record(conn, "silent", "t5", input_tokens=100, cache_read=0)
    assert t.metrics("t5").cache_hit_ratio == pytest.approx(0.9)
    assert t.cache_alarm("t5") is None


def test_the_capability_is_declared_never_inferred_from_the_adapter():
    """The same server behind two protocols reports it on one and not the
    other, so the adapter class is the wrong question — and asking it would
    be a fourth instance of the class-name defect."""
    cfg = Config(
        profile="local",
        backends={
            "native": BackendConfig(kind="ollama", model="m", trust_tier="local",
                                    endpoint="http://localhost:11435"),
            "compat": BackendConfig(kind="openai", model="m", trust_tier="local",
                                    base_url="http://localhost:11435/v1",
                                    reports_prompt_cache=True),
        },
        autonomy={"auto_review": False},
    )
    assert cache_reporting_backend_names(cfg) == {"compat"}
