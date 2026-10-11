from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_RECORDS = [
    ROOT / "agent.local.db",
    ROOT / "runs" / "verify-2026-09-16" / "agent.verify.db",
    ROOT / "docs" / "evidence" / "g1-ablation-2026-09-17.db",
]

ROWS: list[tuple[str, str, str, str]] = [
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
    ("the ceiling budgets against a fitted estimator", "tokens.calibration", "fitted", ""),
    ("a follow-up is given its conversation", "conversation.history", "event", ""),
    ("a final answer is held back for a check", "verify.continued", "event", ""),
    ("a repeated call is warned about", "stuck.warned", "event", ""),
    ("a repeated call halts the task", "stuck.detected", "event", ""),
    ("a step is checkpointed in the shadow repo", "checkpoint.shadow", "event", ""),
    ("a checkpoint could not be taken", "checkpoint.skipped", "event", ""),
    ("a task is rewound", "task.rewound", "event", ""),
    ("the user's AGENTS.md enters the prefix", "agents_md.loaded", "event", ""),
    ("an AGENTS.md the task could write is left out", "agents_md.skipped", "event", ""),
    ("a configured hook runs", "hook.ran", "event", ""),
    ("a pre_tool hook refuses a call", "hook.denied", "event", ""),
    ("a stop hook holds an answer back", "hook.stop_blocked", "event", ""),

    ("compaction tombstones an artifact", "session.opened", "tombstoned", ""),
    ("a session BOUNDARY is crossed", "session.opened", "rotate", ""),
    ("a session is opened at all", "session.opened", "event", ""),
    ("the quarantined summarizer runs", "compress_tool_output", "call", ""),
    ("the semantic cache holds anything", "semantic_cache", "table", ""),
    ("G1: a run with a mechanism ablated", "compaction.skipped", "event", ""),
    ("delegation opens a child", "delegation.opened", "event", ""),

    ("tier-1 memory is written", "memory.written", "event", ""),
    ("tier-1 memory is consolidated", "memory.consolidated", "event", ""),
    ("a pre-compaction flush is refused", "memory.flush_refused", "event", ""),
    ("tier-1 tampering is detected", "memory.tampered", "event", ""),
    ("a parked task's tail is restored", "task.seed_restored", "event", ""),

    ("web_fetch over a real network", "web_fetch", "tool", ""),
    ("web_search over a real network", "web_search", "tool", ""),
    ("a scraper parses to nothing", "tool.search_failed", "event", ""),
    ("run_code in the sandbox", "run_code", "tool", ""),
    ("git_push", "git_push", "tool", ""),

    ("the gate PROMOTES a skill", "promoted,demoted", "skill_status", ""),
    ("decay demotes a skill", "demoted", "skill_status", ""),
    ("a paired trial is recorded", "skill_trials", "table", ""),

    ("a schedule fires", "schedule.fired", "event", ""),
    ("a notification is raised", "notification.raised", "event", ""),
    ("the Telegram channel receives", "channel_updates", "table", ""),
    ("a stranger's message is dropped", "channel_drops", "table", ""),
    ("the out-of-band monitor finds something", "monitor_flags", "table", ""),

    ("a model call stalls", "task.model_stalled", "event", ""),
    ("a task crashes", "task.crashed", "event", ""),

    ("bucephalus snapshot (the backup API)", "writes a FILE", "none", ""),
    ("the TUI", "paints a terminal", "none", ""),
    ("the control plane: server, SSE, console", "answers HTTP", "none", ""),
    ("the POSIX rlimit branch", "ran in WSL2 only (docs/evidence/wsl-2026-09-25)", "none", ""),
    ("the Anthropic adapter, live", "needs a key (D26)", "none", ""),
    ("the Telegram transport, live", "needs a token (D26)", "none", ""),
]


KNOWN_ELSEWHERE = {
    "releasing a decided approval": "docs/evidence/part3-real-runs.json",
    "the pin is re-verified per step": "docs/evidence/part3-real-runs.json",
    "integrity.resume verifies a chain": "docs/evidence/part3-real-runs.json",
}


def _count(conn: sqlite3.Connection, kind: str, what: str) -> int | None:
    try:
        if kind == "event":
            return conn.execute(
                "SELECT COUNT(*) FROM events WHERE type=?", (what,)).fetchone()[0]
        if kind == "fitted":
            return sum(1 for (p,) in conn.execute(
                "SELECT payload_json FROM events WHERE type=?", (what,))
                if json.loads(p).get("fitted"))
        if kind == "rotate":
            return sum(
                1 for (p,) in conn.execute(
                    "SELECT payload_json FROM events WHERE type=?", (what,))
                if json.loads(p).get("parent_id"))
        if kind == "tool":
            return sum(
                1 for (p,) in conn.execute(
                    "SELECT payload_json FROM events WHERE type='tool.receipt'")
                if json.loads(p).get("tool") == what)
        if kind == "tombstoned":
            return sum(
                1 for (p,) in conn.execute(
                    "SELECT payload_json FROM events WHERE type=?", (what,))
                if (json.loads(p).get("tombstoned") or 0) > 0)
        if kind == "skill_status":
            states = what.split(",")
            return conn.execute(
                f"SELECT COUNT(*) FROM skills WHERE status IN ({','.join('?' * len(states))})",
                states).fetchone()[0]
        if kind == "call":
            return conn.execute(
                "SELECT COUNT(*) FROM calls WHERE purpose=?", (what,)).fetchone()[0]
        if kind == "table":
            return conn.execute(f"SELECT COUNT(*) FROM {what}").fetchone()[0]
    except sqlite3.Error:
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
