"""FINDINGS 1.25, as a command instead of an hour.

A unit test proves a function works and says nothing about whether the system
has ever CALLED it for real (1.21). 1.25 is the inventory that asks the second
question, taken against the measurement record rather than the suite - and it
was taken by hand, which is why it is a snapshot dated 2026-09-14 rather than
a fact anyone re-checks.

That is this project's documented failure mode. Three stale-documentation
defects, each found by running the thing the document described. So the
inventory moves here, beside the query that regenerates it:

    python bench/inventory.py --db agent.local.db

Every row is one question - "has this path ever run in production?" - with the
signal that would prove it, counted straight out of the record. Re-running it
after real use is the cheapest way to close rows in the whole project, because
almost nothing here has failed to run for a technical reason; it has failed to
run because nobody has used it.

THREE STATES, NOT TWO. `ran`, `never`, and `not observable in the record`.
Collapsing the third into `never` is the mistake this file exists to avoid:
`alpha snapshot` writes a file, the TUI paints a terminal, the control plane
answers HTTP, and NONE of them leaves a row here. Reporting those as "never
ran" would be a false negative that reads exactly like a true one - and
reporting them as "ran" would be worse. Empty means nothing (DR4).

Read-only. It opens the database, counts, and prints.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every record made by a REAL model. `runs/demo-*` are scripted-model runs -
# real harness, no real model - and 1.25 counts them as tests, so they are
# not here: including them would turn rows green on the strength of a mock.
DEFAULT_RECORDS = [
    ROOT / "agent.local.db",                                # 283 tasks, the
                                                            # main record
    ROOT / "runs" / "verify-2026-09-16" / "agent.verify.db",  # the 5-task
                                                            # verification run
    # The G1 ablation arms, 2026-09-17. HARNESS_VERSION 3 where the other two
    # are 2 - which makes their NUMBERS incomparable and is exactly what a bump
    # is for, but this file asks which PATHS have been exercised, and a path
    # that ran is a path that ran whatever the measurement rules were.
    ROOT / "docs" / "evidence" / "g1-ablation-2026-09-17.db",
]

# (row, what would prove it, kind, argument)
#
#   event  - an event type on the hash chain
#   tool   - a `tool.receipt` naming this tool
#   call   - a row in `calls` with this purpose
#   table  - any row in this table
#   rotate - a `session.opened` that has a parent, i.e. a BOUNDARY rather
#            than the first session every task opens
#   none   - leaves no trace in the record; see the module docstring
ROWS: list[tuple[str, str, str, str]] = [
    # -- the loop's own guarantees ----------------------------------------
    ("integrity.resume verifies a chain", "integrity.verified", "event", ""),
    ("a chain break halts the task", "integrity.halt", "event", ""),
    ("the plan pin's anchor half", "plan.approved", "event", ""),
    ("the pin is re-verified per step", "plan.pin_verified", "event", ""),
    ("releasing a decided approval", "approval.consumed", "event", ""),
    ("the capability gate REFUSES something", "human.denied", "event", ""),
    ("a human approves something", "human.approved", "event", ""),
    ("a tool is refused by exposure", "tool.not_exposed", "event", ""),
    ("the deputy decides", "review.decided", "event", ""),
    ("a task is steered (forked)", "human.steered", "event", ""),
    ("deviation from the plan is detected", "deviation.detected", "event", ""),

    # -- context management: G1's mechanisms ------------------------------
    ("compaction tombstones an artifact", "artifact.tombstoned", "event", ""),
    ("a session BOUNDARY is crossed", "session.opened", "rotate", ""),
    ("a session is opened at all", "session.opened", "event", ""),
    ("the quarantined summarizer runs", "compress_tool_output", "call", ""),
    ("the semantic cache holds anything", "semantic_cache", "table", ""),
    ("G1: a run with a mechanism ablated", "compaction.skipped", "event", ""),
    ("delegation opens a child", "delegation.opened", "event", ""),

    # -- memory ------------------------------------------------------------
    ("tier-1 memory is written", "memory.written", "event", ""),
    ("tier-1 memory is consolidated", "memory.consolidated", "event", ""),
    ("a pre-compaction flush is refused", "memory.flush_refused", "event", ""),
    ("tier-1 tampering is detected", "memory.tampered", "event", ""),
    ("a parked task's tail is restored", "task.seed_restored", "event", ""),

    # -- tools that reach outside -----------------------------------------
    ("web_fetch over a real network", "web_fetch", "tool", ""),
    ("web_search over a real network", "web_search", "tool", ""),
    ("a scraper parses to nothing", "tool.search_failed", "event", ""),
    ("run_code in the sandbox", "run_code", "tool", ""),
    ("git_push", "git_push", "tool", ""),

    # -- skills ------------------------------------------------------------
    ("the gate PROMOTES a skill", "skill.promoted", "event", ""),
    ("decay demotes a skill", "skill.demoted", "event", ""),
    ("a paired trial is recorded", "skill_trials", "table", ""),

    # -- cron, channel, monitor -------------------------------------------
    ("a schedule fires", "schedule.fired", "event", ""),
    ("a notification is raised", "notification.raised", "event", ""),
    ("the Telegram channel receives", "channel_updates", "table", ""),
    ("a stranger's message is dropped", "channel_drops", "table", ""),
    ("the out-of-band monitor finds something", "monitor_flags", "table", ""),

    # -- machine faults, which must never be task outcomes ----------------
    ("a model call stalls", "task.model_stalled", "event", ""),
    ("a task crashes", "task.crashed", "event", ""),

    # -- leaves no trace here ---------------------------------------------
    ("alpha snapshot (the backup API)", "writes a FILE", "none", ""),
    ("the TUI", "paints a terminal", "none", ""),
    ("the control plane: server, SSE, console", "answers HTTP", "none", ""),
    ("the POSIX rlimit branch", "never run on any platform here", "none", ""),
    ("the Anthropic adapter, live", "needs a key (D26)", "none", ""),
    ("the Telegram transport, live", "needs a token (D26)", "none", ""),
]


# Rows whose evidence exists but NOT in any database this can read.
#
# Part 3 ran each of these in a real process against its own database under
# `ALPHA_DATA_DIR`, on purpose - `agent.local.db` untouched - and `runs/` is
# gitignored, so that database is gone while `docs/evidence/part3-real-runs*`
# records what it did. Counted straight, these three read `never`, which is a
# FALSE NEGATIVE that looks exactly like a true one. An instrument that
# cannot see a record must say so rather than answer for it.
KNOWN_ELSEWHERE = {
    "releasing a decided approval": "docs/evidence/part3-real-runs.json",
    "the pin is re-verified per step": "docs/evidence/part3-real-runs.json",
    "integrity.resume verifies a chain": "docs/evidence/part3-real-runs.json",
}


def _count(conn: sqlite3.Connection, kind: str, what: str) -> int | None:
    """How many times this happened. None when the record cannot say."""
    try:
        if kind == "event":
            return conn.execute(
                "SELECT COUNT(*) FROM events WHERE type=?", (what,)).fetchone()[0]
        if kind == "rotate":
            # A boundary, not the first session every task opens at start.
            return sum(
                1 for (p,) in conn.execute(
                    "SELECT payload_json FROM events WHERE type=?", (what,))
                if json.loads(p).get("parent_id"))
        if kind == "tool":
            return sum(
                1 for (p,) in conn.execute(
                    "SELECT payload_json FROM events WHERE type='tool.receipt'")
                if json.loads(p).get("tool") == what)
        if kind == "call":
            return conn.execute(
                "SELECT COUNT(*) FROM calls WHERE purpose=?", (what,)).fetchone()[0]
        if kind == "table":
            return conn.execute(f"SELECT COUNT(*) FROM {what}").fetchone()[0]
    except sqlite3.Error:
        # A table this database does not have is not the same as a table with
        # nothing in it. An older record predates some of these.
        return None
    return None


def take(db: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = []
        for row, what, kind, _arg in ROWS:
            n = None if kind == "none" else _count(conn, kind, what)
            if kind == "none":
                state = "not observable"
            elif n is None:
                state = "no such table"
            elif n > 0:
                state = "ran"
            else:
                state = "never"
            out.append({"row": row, "signal": what, "kind": kind,
                        "n": n, "state": state})
        return out
    finally:
        conn.close()


def union(dbs: list[Path]) -> list[dict]:
    """The inventory across several records, summed.

    A path has run for real if it ran in ANY of them - 1.25 is taken against
    the record AND the evidence, and the verification run of 2026-09-16 is
    where sessions first opened for real while `agent.local.db` still shows
    none. Reading one database alone would report that row as `never` and be
    wrong in the direction that reads like diligence.
    """
    per = [take(d) for d in dbs]
    out = []
    for i, base in enumerate(per[0]):
        ns = [p[i]["n"] for p in per]
        real = [n for n in ns if n is not None]
        total = sum(real) if real else None
        if base["kind"] == "none":
            state = "not observable"
        elif total is None:
            state = "no such table"
        elif total > 0:
            state = "ran"
        elif base["row"] in KNOWN_ELSEWHERE:
            state = "ran elsewhere"
        else:
            state = "never"
        out.append({**base, "n": total, "state": state,
                    "elsewhere": KNOWN_ELSEWHERE.get(base["row"])})
    return out


def context(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        def one(sql):
            try:
                return conn.execute(sql).fetchone()[0]
            except sqlite3.Error:
                return None
        return {
            "db": str(db),
            "tasks": one("SELECT COUNT(*) FROM tasks"),
            "events": one("SELECT COUNT(*) FROM events"),
            "model_calls_logged": one(
                "SELECT COUNT(*) FROM events WHERE type='model.called'"),
            "calls_rows": one("SELECT COUNT(*) FROM calls"),
            "backends": sorted({r[0] for r in conn.execute(
                "SELECT DISTINCT backend FROM calls")}) or None,
            "first_task": one("SELECT MIN(created_at) FROM tasks"),
            "last_task": one("SELECT MAX(created_at) FROM tasks"),
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="FINDINGS 1.25 re-run: what has ever run for real")
    p.add_argument("--db", action="append", default=None, metavar="PATH",
                   help="a record to read; repeatable. Default: every "
                        "REAL-MODEL record (the demo databases are "
                        "scripted-model runs and count as tests, per 1.25)")
    p.add_argument("--json", action="store_true", help="machine-readable")
    p.add_argument("--only", choices=["ran", "never", "ran elsewhere", "not observable"],
                   help="print one state only; the summary still counts all")
    args = p.parse_args(argv)

    dbs = [Path(d) for d in (args.db or [str(p) for p in DEFAULT_RECORDS])]
    missing = [d for d in dbs if not d.exists()]
    if missing:
        print(f"no such database: {', '.join(str(m) for m in missing)}",
              file=sys.stderr)
        return 2

    rows = union(dbs)
    contexts = [context(d) for d in dbs]

    if args.json:
        print(json.dumps({"records": contexts, "rows": rows}, indent=2))
        return 0

    for c in contexts:
        print(f"{c['db']}: {c['tasks']} tasks, {c['events']} events, "
              f"{c['calls_rows']} call rows against {c['model_calls_logged']} "
              f"model.called, backends={c['backends']}")
    print()
    shown = [r for r in rows if not args.only or r["state"] == args.only]
    w = max((len(r["row"]) for r in shown), default=10)
    for r in shown:
        n = "-" if r["n"] is None else f"{r['n']:,}"
        where = r.get("elsewhere") if r["state"] == "ran elsewhere" else r["signal"]
        print(f"  {r['row']:<{w}}  {r['state']:<14} {n:>8}  {where}")

    # The summary counts EVERY row, never only the filtered ones: a `--only
    # ran` view that reported "3 of 3" would be a denominator chosen by the
    # filter, which is the shape of an adjacency defect (C3).
    counted = [r for r in rows if r["state"] in ("ran", "never")]
    ran = sum(1 for r in counted if r["state"] == "ran")
    elsewhere = sum(1 for r in rows if r["state"] == "ran elsewhere")
    unobservable = sum(1 for r in rows if r["state"] == "not observable")
    print(f"\n{ran} of {len(counted)} observable paths have run for real "
          f"across {len(dbs)} record(s). {elsewhere} ran in a record this "
          f"cannot read (see the path beside each). {unobservable} leave no "
          f"trace in a database at all and are listed as such rather than "
          f"counted either way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
