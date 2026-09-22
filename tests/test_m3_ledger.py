"""M3 acceptance — prefix stability, deletion-based compaction, taint-safe cache."""

from __future__ import annotations

import pytest

from agent.kernel.types import Taint
from agent.ledger.compact import compact, enforce_ceiling, extract_exact_strings
from agent.ledger.entries import LedgerEntry
from agent.ledger.prefix import PrefixBuilder, PrefixGuard, PrefixMutationError, SYSTEM_CONTRACT
from agent.ledger.semantic_cache import cached_op

SKILLS = [
    {"name": "beta", "version": 2, "body": "## do the thing\nstep one  "},
    {"name": "alpha", "version": 1, "body": "## other thing\nstep two"},
]
CONTRACT = {"goal": "g", "artifact_scope": ["work"], "success_criterion": "always_pass"}


def test_prefix_byte_stable_across_20_turns():
    """Build the prefix 20 times with the same inputs in shuffled order."""
    import random

    fps = set()
    for i in range(20):
        skills = SKILLS[:]
        random.Random(i).shuffle(skills)
        contract = dict(reversed(list(CONTRACT.items())))
        fps.add(PrefixBuilder(SYSTEM_CONTRACT, skills, contract).fingerprint())
    assert len(fps) == 1


def test_prefix_guard_catches_mutation():
    guard = PrefixGuard()
    guard.check(PrefixBuilder(SYSTEM_CONTRACT, SKILLS, CONTRACT), "t1")
    promoted_mid_task = SKILLS + [{"name": "gamma", "version": 1, "body": "## new"}]
    with pytest.raises(PrefixMutationError):
        guard.check(PrefixBuilder(SYSTEM_CONTRACT, promoted_mid_task, CONTRACT), "t1")


def test_prefix_rejects_a_timestamp_in_the_contract():
    """The classic way to lose the discount: a date in the stable section.

    The timestamp goes in `goal`, a field the model actually sees. This test
    injected `{"now": ...}` as a bare contract key until 2026-09-22, and
    stopped firing when the prefix moved to an ALLOWLIST of contract fields
    (the grader leak, HARNESS_VERSION 4): an unrecognised key cannot reach the
    prefix now, so it cannot move it either.

    That is one route closing, not the invariant weakening - anything varying
    inside a visible field still voids the discount and still raises. The
    companion below pins the closed route so the two cannot be confused.
    """
    guard = PrefixGuard()
    guard.check(PrefixBuilder(SYSTEM_CONTRACT, SKILLS,
                              {**CONTRACT, "goal": "report at 10:00"}), "t")
    with pytest.raises(PrefixMutationError):
        guard.check(PrefixBuilder(SYSTEM_CONTRACT, SKILLS,
                                  {**CONTRACT, "goal": "report at 10:01"}), "t")


def test_an_unrecognised_contract_key_cannot_reach_the_prefix_at_all():
    """Why the test above changed route. An arbitrary key is not rendered, so
    it can neither leak nor move the prefix. See
    `tests/test_grader_is_not_in_the_prompt.py` for the defect that bought
    this and the allowlist it has to maintain."""
    a = PrefixBuilder(SYSTEM_CONTRACT, SKILLS, {**CONTRACT, "now": "10:00"})
    b = PrefixBuilder(SYSTEM_CONTRACT, SKILLS, {**CONTRACT, "now": "10:01"})
    assert a.fingerprint() == b.fingerprint()
    assert "10:00" not in a.build()


def test_compaction_preserves_exact_strings():
    trace = (
        'Traceback (most recent call last):\n'
        '  File "src/parser.py", line 88, in parse\n'
        "    raise TypeError('bad node')\n"
        "TypeError: bad node at commit 4f9a2b1c3d4e5f6\n"
        "version 2.11.3\n" + ("filler line\n" * 400)
    )
    entries = [
        LedgerEntry.note("pinned goal", pinned=True),
        LedgerEntry.observation(trace, artifact_id="a3f9c2", taint=Taint.UNTRUSTED),
        LedgerEntry.observation("recent and small", taint=Taint.UNTRUSTED),
    ]
    out = compact(entries, target_tokens=40)
    tomb = [e for e in out if e.kind == "tombstone"]
    assert tomb, "nothing was tombstoned"
    body = tomb[0].content
    assert "src/parser.py" in body
    assert "line 88" in body
    assert "TypeError" in body
    assert "a3f9c2" in body and "expand_artifact" in body
    assert any(e.pinned for e in out), "pinned entries must survive"


def test_compaction_never_paraphrases_survivors():
    keep = LedgerEntry.observation("exact survivor text", taint=Taint.UNTRUSTED)
    out = compact([keep], target_tokens=10_000)
    assert out[0].content == "exact survivor text"


def test_extract_exact_strings_finds_identifiers():
    found = extract_exact_strings(
        "see C:\\work\\out.py and /usr/lib/x, ValueError: nope, line 12, v1.2.3"
    )
    joined = " ".join(found)
    assert "line 12" in joined and "v1.2.3" in joined


def test_ceiling_triggers_delegation_not_squeeze():
    # Small tail, huge prefix: the work itself is too big -> delegate.
    assert enforce_ceiling(60_000, 1_000, 200_000, 0.28) == "delegate"
    # Fat tail -> compact.
    assert enforce_ceiling(1_000, 60_000, 200_000, 0.28) == "compact"
    assert enforce_ceiling(1_000, 1_000, 200_000, 0.28) == "ok"


def test_cache_hit_ratio_above_threshold(ctx, task, scripted):
    """Run a multi-turn task; most input tokens should be served from cache."""
    from agent.execute.loop import run_task

    scripted(
        "cloud",
        '<action tool="list_dir">{"path": "work"}</action>',
        '<action tool="list_dir">{"path": "work"}</action>',
        '<action tool="list_dir">{"path": "work"}</action>',
        '<final>{"answer": "done"}</final>',
    )
    run_task(ctx, task.id)
    m = ctx.telemetry.metrics(task.id)
    assert m.cache_hit_ratio > 0.5, f"cache hit ratio collapsed to {m.cache_hit_ratio}"
    assert ctx.telemetry.cache_alarm(task.id) is None


def test_cache_does_not_launder_taint(ctx):
    """Store an untrusted result, request it in a trusted context, assert miss."""
    calls = []

    def fn():
        calls.append(1)
        return {"summary": "from a web page"}

    cached_op(ctx.conn, "summarize", "payload text", Taint.UNTRUSTED, fn)
    assert len(calls) == 1

    _, hit = cached_op(ctx.conn, "summarize", "payload text", Taint.TRUSTED, fn)
    assert hit is False, "a trusted read got an untrusted cache entry"
    assert len(calls) == 2

    _, hit2 = cached_op(ctx.conn, "summarize", "payload text", Taint.UNTRUSTED, fn)
    assert hit2 is True


def test_the_mock_cache_has_an_opinion_about_the_bytes(ctx):
    """The mock stands in for the server whose cache_hit_ratio is the
    independent check on prefix stability. Keyed on the bytes, or it checks
    nothing - see MockBackend.complete."""
    b = ctx.backends["cloud"]
    first = b.complete("PREFIX-A", "t1").usage
    again = b.complete("PREFIX-A", "t2").usage
    other = b.complete("PREFIX-B", "t3").usage
    assert first.cache_read_tokens == 0 and first.cache_write_tokens > 0
    assert again.cache_read_tokens > 0 and again.cache_write_tokens == 0
    assert other.cache_read_tokens == 0, "a different prefix must be a miss"
