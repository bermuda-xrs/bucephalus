"""The hash chain, pinned where mutation testing found it unpinned
(iteration 3, 2.3; scripts/mutate.py on kernel/events.py and integrity.py).

Each test names the mutant it kills. Before these, each change left the
chain's whole test set green.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from agent.kernel.events import EVENT_TYPES, canonical, sha256_hex
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"


def _task(ctx):
    return create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=60))


def test_a_forged_row_with_its_own_valid_hash_breaks_the_chain(ctx):
    """Kills the previous-link check returning None or True: a row rewritten
    with a recomputed, self-consistent hash passes the hash check, and ONLY
    the link to its predecessor catches it."""
    t = _task(ctx)
    for i in range(3):
        ctx.log.append(t.id, "tool.receipt", {"tool": "read_file", "key": f"k{i}", "ok": True})
    seq = ctx.conn.execute("SELECT MAX(seq) FROM events WHERE task_id=?", (t.id,)).fetchone()[0]
    forged_prev = "f" * 64
    payload = {"tool": "write_file", "key": "forged", "ok": True}
    body = canonical({"task_id": t.id, "seq": seq, "type": "tool.receipt", "payload": payload})
    ctx.conn.execute("UPDATE events SET payload_json=?, prev_hash=?, hash=? WHERE task_id=? AND seq=?",
                     (canonical(payload), forged_prev, sha256_hex(forged_prev + body), t.id, seq))
    ctx.conn.commit()
    assert ctx.log.verify(t.id) == (False, seq)


def test_every_chain_starts_from_the_recorded_genesis(ctx):
    """Kills GENESIS changed: like the canonical form, a change on both
    sides still verifies new chains and orphans every recorded one."""
    t = _task(ctx)
    first = ctx.conn.execute("SELECT prev_hash FROM events WHERE task_id=? AND seq=0",
                             (t.id,)).fetchone()[0]
    assert first == "0" * 64


def test_the_canonical_form_is_pinned_byte_for_byte():
    """Kills ensure_ascii True -> False: both sides of the chain would change
    together, so every chain would still verify - except every record already
    written, whose hashes were taken over the escaped form. The evidence
    databases in docs/evidence would stop verifying."""
    got = canonical({"b": [1, 2.5], "a": "é∆", "z": None})
    assert got == '{"a":"\\u00e9\\u2206","b":[1,2.5],"z":null}'


def test_events_default_to_the_whole_history(ctx):
    """Kills events(since_seq=-1 -> 0): the default is the WHOLE history,
    task.created included."""
    t = _task(ctx)
    evs = ctx.log.events(t.id)
    assert evs and evs[0]["seq"] == 0 and evs[0]["type"] == "task.created"


def _appended_types() -> set[str]:
    """Every literal event type the source passes to `<x>.log.append(...)` or
    `log.append(...)` - read from the AST, not grepped."""
    out = set()
    for f in SRC.rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "append" and len(n.args) >= 2
                    and ast.unparse(n.func.value).endswith("log")):
                # The type may be a literal or a choice between literals:
                # "human.approved" if approve else "human.denied".
                for c in ast.walk(n.args[1]):
                    if (isinstance(c, ast.Constant) and isinstance(c.value, str)
                            and re.fullmatch(r"[a-z_]+\.[a-z_]+", c.value)):
                        out.add(c.value)
    return out


def test_every_event_the_code_records_is_registered():
    """Kills each EVENT_TYPES member dropped: appending an unregistered type
    raises, so a dropped type is a code path that crashes the first time it
    runs - found here, not in production."""
    appended = _appended_types()
    assert len(appended) > 20, "the AST scan found too few appends to mean anything"
    missing = appended - EVENT_TYPES
    assert not missing, f"recorded but not registered: {sorted(missing)}"


def test_deleting_a_checkpointed_task_is_caught(ctx):
    """D60, and the Anchor.all_tasks mutants: a task whose row and events
    are both deleted still has its checkpoint in the anchor, kept outside the
    database - which is what the anchor is for."""
    from agent.kernel.integrity import checkpoint, verify_all

    t = _task(ctx)
    checkpoint(ctx.log, ctx.anchor, t.id)
    assert not [f for f in verify_all(ctx.conn, ctx.log, ctx.anchor) if f.get("task_id") == t.id]
    ctx.conn.execute("DELETE FROM events WHERE task_id=?", (t.id,))
    ctx.conn.execute("DELETE FROM tasks WHERE id=?", (t.id,))
    ctx.conn.commit()
    found = [f for f in verify_all(ctx.conn, ctx.log, ctx.anchor) if f.get("task_id") == t.id]
    assert found and found[0]["severity"] == "critical", found


def test_a_plan_pin_is_not_mistaken_for_a_deleted_task(ctx):
    """The anchor also holds `plan:<task>` pins; they name no task row."""
    from agent.kernel.integrity import verify_all

    ctx.anchor.record("plan:t_whatever", "a" * 64)
    assert not [f for f in verify_all(ctx.conn, ctx.log, ctx.anchor)
                if "plan:" in str(f.get("task_id", ""))]


def test_append_returns_the_new_head(ctx):
    """Kills append's `return h` -> None: callers checkpoint what it returns."""
    t = _task(ctx)
    h = ctx.log.append(t.id, "tool.receipt", {"tool": "read_file", "key": "k", "ok": True})
    assert h == ctx.log.head(t.id) and len(h) == 64


def test_a_gap_is_caught_even_when_the_rest_is_rehashed(ctx):
    """Kills the sequence check returning None or True. A forger who deletes
    a row and re-hashes everything after it leaves every link and every hash
    self-consistent; the missing sequence number is all that is left."""
    t = _task(ctx)
    for i in range(3):
        ctx.log.append(t.id, "tool.receipt", {"tool": "read_file", "key": f"k{i}", "ok": True})
    ctx.conn.execute("DELETE FROM events WHERE task_id=? AND seq=1", (t.id,))
    rows = ctx.conn.execute("SELECT seq, type, payload_json FROM events WHERE task_id=? "
                            "ORDER BY seq", (t.id,)).fetchall()
    prev = ctx.conn.execute("SELECT hash FROM events WHERE task_id=? AND seq=0",
                            (t.id,)).fetchone()[0]
    for seq, etype, pj in rows[1:]:
        body = canonical({"task_id": t.id, "seq": seq, "type": etype, "payload": json.loads(pj)})
        h = sha256_hex(prev + body)
        ctx.conn.execute("UPDATE events SET prev_hash=?, hash=? WHERE task_id=? AND seq=?",
                         (prev, h, t.id, seq))
        prev = h
    ctx.conn.commit()
    assert ctx.log.verify(t.id) == (False, 2)


def test_only_a_chain_head_counts_as_one(ctx):
    """Kills `_is_chain_hash`'s `and` -> `or`: a short hex string or a
    64-character non-hex value is not a chain head, whichever test it passes."""
    t = _task(ctx)
    ctx.anchor.record(t.id, "a" * 64)
    ctx.anchor.record(t.id, "deadbeef")
    ctx.anchor.record(t.id, "plan:" + "b" * 59)
    assert ctx.anchor.latest_chain(t.id) == "a" * 64


def test_the_anchor_creates_its_own_directory(tmp_path):
    """Kills mkdir(parents=True -> False): the anchor's default home is a
    nested directory under the user data dir that may not exist yet."""
    from agent.kernel.integrity import Anchor

    a = Anchor(tmp_path / "a" / "b" / "chain.anchor")
    a.record("t", "c" * 64)
    assert a.latest_chain("t") == "c" * 64
