"""What tier-1 memory's integrity check is, stated as the code does it
(AUDIT-2026-09 F6, D44).

The store claimed "the AUTHENTICATED record is the hash chain", and nothing
compared against the chain - an inert guarantee. Wiring it as an ALARM is
wrong, measured: memory is one store per user, shared by every config, while
each config's chain is its own database, and `alpha memory --accept` blesses a
state with no chain event at all. A load-time comparison would flag another
config's legitimate write - or any accepted state - as tampering: DR1 width,
the same defect the per-config anchor was withdrawn for. So the claim is
corrected instead, and this test pins the design against a naive wiring.
"""

from __future__ import annotations

from pathlib import Path

from agent.context import AgentContext
from agent.kernel.integrity import verify_memory
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.memory.store import MemoryStore, record_fingerprint

from conftest import make_config


def _ctx(tmp_path, name, memory_dir):
    d = tmp_path / name
    d.mkdir()
    ctx = AgentContext(make_config(d))
    ctx.memory = MemoryStore(memory_dir)            # one store, as in production
    return ctx


def test_memory_written_under_one_config_is_intact_under_another(tmp_path):
    shared = tmp_path / "memory"
    a = _ctx(tmp_path, "config_a", shared)
    record_fingerprint(a.memory)
    task = create_task(a.conn, a.log, TaskContract(goal="remember", artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=60))
    out = a.tools.get("memory_write").fn(a, task, text="the deploy port is 8443")
    assert out["ok"], out
    b = _ctx(tmp_path, "config_b", shared)          # its chain never saw that write
    assert verify_memory(b.memory) is None, (
        "a legitimate write through another config reads as tampering here")
    a.close()
    b.close()


def test_the_store_no_longer_claims_an_authentication_it_does_not_perform():
    text = (Path(__file__).resolve().parents[1] / "src" / "agent" / "memory"
            / "store.py").read_text(encoding="utf-8")
    assert "The AUTHENTICATED record is the hash chain" not in text
