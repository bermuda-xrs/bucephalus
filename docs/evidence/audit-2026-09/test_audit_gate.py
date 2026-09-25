"""AUDIT-2026-09 evidence: capability gate and approval surface.

Run from the repo root:
  python -m pytest docs/evidence/audit-2026-09/test_audit_gate.py -q -p no:cacheprovider

Each test asserts the CURRENT (defective) behaviour, so a green run is the
demonstration; a fix turns it red.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import make_config  # noqa: E402

from agent.context import AgentContext  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, Taint, TaskContract  # noqa: E402
from agent.security.capabilities import gate_tool_call, render_preview  # noqa: E402
from agent.security.taint import TaintTracker  # noqa: E402


def _ctx_task(tmp_path, scope=("work",)):
    ctx = AgentContext(make_config(tmp_path))
    (tmp_path / "work").mkdir(exist_ok=True)
    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=list(scope)),
                    Budget(usd=0, seconds=60))
    return ctx, t


def test_F_empty_args_are_trusted():
    """DR4: a call with no arguments is TRUSTED, the most trusted level."""
    assert TaintTracker().args_taint({}) == Taint.TRUSTED
    tr = TaintTracker()
    tr.observe("page text", Taint.UNTRUSTED)     # the task has read a web page
    assert tr.args_taint({}) == Taint.TRUSTED    # ...and {} is still TRUSTED


def test_F_side_effecting_call_with_no_args_skips_approval(tmp_path):
    """memory_consolidate is side-effecting (evicts tier-1 entries); called with
    {} it is ALLOWED outright, where the same call naming its default tier
    parks for approval."""
    ctx, t = _ctx_task(tmp_path)
    tool = ctx.tools.get("memory_consolidate")
    ctx.taint_tracker(t.id).observe("some fetched page", Taint.UNTRUSTED)
    d_empty = gate_tool_call(ctx, t, tool, {})
    d_named = gate_tool_call(ctx, t, tool, {"tier": "memory"})
    print("empty:", d_empty, "\nnamed:", d_named)
    assert d_empty.verdict == "allow"
    assert d_named.verdict == "needs_approval"
    ctx.close()


def test_F_human_sees_77_chars_of_run_code():
    """The approval preview a human decides on shows at most 77 characters of
    the program run_code will execute."""
    class T:
        name = "run_code"
    code = "# harmless looking header comment that fills the preview\n" + "x = 1\n" * 50 \
        + "HIDDEN_TAIL = True\n"
    p = render_preview(T, {"code": code})
    print(repr(p), len(p))
    assert "HIDDEN_TAIL" not in p
    assert len(p) <= len("run_code(code=") + 80 + 1


def test_F_memory_integrity_never_consults_the_chain(tmp_path):
    """store.py:208 calls the hash chain 'the AUTHENTICATED record', and the
    memory.written events carry a fingerprint - but check_fingerprint and
    verify_all compare the files only with `.fingerprint`, a plain sha256 kept
    IN the same directory. Rewrite both and nothing notices, while the chained
    value disagrees. No src/ code reads the chained fingerprint."""
    from agent.kernel.integrity import verify_all
    from agent.memory.store import INTACT, check_fingerprint, record_fingerprint

    ctx, t = _ctx_task(tmp_path)
    ctx.memory.root.mkdir(parents=True, exist_ok=True)
    (ctx.memory.root / "MEMORY.md").write_text("- [user] blessed fact" + chr(10),
                                               encoding="utf-8")
    chained = record_fingerprint(ctx.memory)
    ctx.log.append(t.id, "memory.written", {"fingerprint": chained})
    # An out-of-band writer edits the file AND re-blesses it.
    (ctx.memory.root / "MEMORY.md").write_text("- [user] injected instruction" + chr(10),
                                               encoding="utf-8")
    (ctx.memory.root / ".fingerprint").write_text(ctx.memory.fingerprint(), encoding="utf-8")
    assert ctx.memory.fingerprint() != chained               # the chain disagrees...
    assert check_fingerprint(ctx.memory) == INTACT           # ...load-time check passes
    assert not [f for f in verify_all(ctx.conn, ctx.log, ctx.anchor, ctx.memory)
                if f["task_id"] == "__memory__"]             # ...and /alerts is silent
    assert "injected instruction" in ctx.freeze_memory(t.id)["memory"]  # ...and it loads
    ctx.close()
