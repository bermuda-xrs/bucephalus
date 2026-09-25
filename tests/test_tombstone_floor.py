"""A long session's compaction floor is bounded (ledger/compact.py).

Tombstones were never dropped, so every boundary started higher: ~28 tokens
per dropped observation. At the shipped window the floor alone crossed the
ceiling by step ~60 and the context reached 14,060 tokens by step 400 - a
correctness failure of compaction, not an efficiency one: it fired every step
and could not get under the ceiling it fired on.
"""

from __future__ import annotations

from agent.kernel.types import Taint
from agent.ledger.compact import INDEX_LABEL, compact
from agent.ledger.entries import LedgerEntry, sum_tokens

WINDOW, PREFIX = 16384, 900
TARGET, CEILING = int(WINDOW * 0.20) - PREFIX, int(WINDOW * 0.28)


def _obs(i, taint=Taint.USER):
    return LedgerEntry(kind="observation", taint=taint, artifact_id=f"a{i:04d}",
                       content=f"work/tickets/t{i}.json: " + "row value 17 " * 60)


def _session(steps, taint_at=None):
    entries = [LedgerEntry.note("objective", pinned=True)]
    worst = 0
    for i in range(1, steps + 1):
        entries.append(_obs(i, Taint.UNTRUSTED if i == taint_at else Taint.USER))
        if PREFIX + sum_tokens(entries) > CEILING:
            entries = compact(entries, target_tokens=TARGET)
            worst = max(worst, PREFIX + sum_tokens(entries))
    return entries, worst


def test_a_long_session_compacts_back_under_the_ceiling_every_time():
    entries, worst = _session(400)
    assert worst <= CEILING, (
        f"after compaction the context was {worst} tokens against {CEILING}: the "
        "tombstone floor has grown past what compaction can remove")
    tomb = sum(e.n_tokens for e in entries if e.kind == "tombstone")
    assert tomb <= int(TARGET * 0.25) + 400, f"tombstones hold {tomb} tokens"


def test_folding_loses_no_observation():
    """Every dropped observation is either its own tombstone or counted in
    the index, and the index names the newest ids."""
    entries, _ = _session(200)
    index = [e for e in entries if e.label == INDEX_LABEL]
    assert len(index) == 1, "expected one index"
    counted = int(index[0].exact_strings[0])
    single = [e for e in entries if e.kind == "tombstone" and e.label != INDEX_LABEL]
    live = [e for e in entries if e.kind == "observation"]
    assert counted + len(single) + len(live) == 200
    ids = index[0].exact_strings[1:]
    assert ids and all(i in index[0].content for i in ids)
    assert ids[-1] > ids[0], "the index should keep the NEWEST folded ids"


def test_folding_does_not_launder_taint():
    """An untrusted observation folded among trusted ones: the index is
    untrusted. Otherwise folding is a way to wash provenance."""
    entries, _ = _session(120, taint_at=3)
    index = [e for e in entries if e.label == INDEX_LABEL][0]
    assert index.taint == Taint.UNTRUSTED, index.taint
