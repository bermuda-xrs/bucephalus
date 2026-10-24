from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

from test_chain_mutants import _appended_types

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("inventory", ROOT / "bench" / "inventory.py")
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def test_every_event_the_inventory_counts_is_one_the_code_records():
    recorded = _appended_types()
    counted = {what for _row, what, kind in inventory.ROWS if kind in ("event", "rotate")}
    assert counted, "the inventory counts no events at all"
    assert counted <= recorded, f"counted but never recorded: {sorted(counted - recorded)}"


def _row(rows, label):
    return next(r for r in rows if r["row"].startswith(label))


def test_a_promotion_a_demotion_and_a_tombstone_all_read_as_ran(ctx):
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    ctx.conn.execute("INSERT INTO skills(id, name, version, body, contract_json, provenance_json, "
                     "status, created_at) VALUES ('s1','a',1,'b','{}','{}','promoted',?), "
                     "('s2','c',1,'d','{}','{}','demoted',?)", (time.time(), time.time()))
    ctx.conn.commit()
    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=60))
    ctx.log.append(t.id, "session.opened", {"session_id": "x", "parent_id": "p",
                                            "reason": "boundary", "tombstoned": 2})
    db = Path(ctx.conn.execute("PRAGMA database_list").fetchone()[2])
    rows = inventory.take(db)
    for label in ("the gate PROMOTES a skill", "decay demotes a skill",
                  "compaction tombstones an artifact"):
        r = _row(rows, label)
        assert r["state"] == "ran", (label, r)
    assert json.dumps(rows)
