"""D49 - the run's recorded limits vs the limit actually applied.

loop._perform puts result["notes"] on the chained tool.receipt as "which limits
held this run". The note is built from the constant MAX_PROCESSES; D49 applies
spawn_denied_processes() to agent code instead."""

from __future__ import annotations

import sys

import pytest

import agent.execute.sandbox as sb
from agent.execute.codeact import run_code_action

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Object path")


def test_FINDING_the_receipt_records_32_processes_for_a_run_capped_at_the_interpreters_own(ctx, task, monkeypatch):
    seen = {}
    real = sb._platform.spawn_limited

    def spy(argv, **kw):
        seen["max_processes"] = kw.get("max_processes")
        return real(argv, **kw)

    monkeypatch.setattr(sb._platform, "spawn_limited", spy)
    res = run_code_action(ctx, task, "print('hi')", timeout_s=30)
    print(f"\napplied max_processes={seen['max_processes']}  recorded notes={res.get('notes')}")
    assert seen["max_processes"] == sb._platform.spawn_denied_processes() <= 2
    assert any("32 processes" in n for n in res["notes"])
