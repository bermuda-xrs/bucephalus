from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


FINAL = '<final>{"answer": "done"}</final>'
FILES = "abcdef"


def READ(name):
    return '<action tool="read_file">' + json.dumps({"path": f"work/{name}.txt"}) + "</action>"


def REMEMBER(text):
    return '<action tool="memory_write">' + json.dumps({"text": text}) + "</action>"


CODE = ("<code>\ntotal = 0\nfor n in 'abcd':\n"
        "    total += len(open(f'work/{n}.txt', encoding='utf-8').read())\n"
        "print(total)\n</code>")


class Script:
    def __init__(self, turns):
        self.turns, self.i = list(turns), 0

    def __call__(self, prefix, tail):
        from agent.execute.loop import FLUSH_NOTE

        if FLUSH_NOTE.strip()[:40] in tail:
            return "NOTHING"
        turn = self.turns[min(self.i, len(self.turns) - 1)]
        self.i += 1
        return turn


def _ctx(tmp: Path, window: int | None = None, items: int = 90, **mechanisms):
    sys.path.insert(0, str(ROOT / "tests"))
    from conftest import make_config

    from agent.context import AgentContext
    from agent.memory.store import MemoryStore, record_fingerprint

    cfg = make_config(tmp)
    for k, v in mechanisms.items():
        setattr(cfg.mechanisms, k, v)
    cfg.backends["local"] = cfg.backends["cloud"].model_copy(
        update={"trust_tier": "local"})
    if window:
        cfg.backends["cloud"].usable_context_window = window
    ctx = AgentContext(cfg)
    ctx.memory = MemoryStore(tmp / "memory")
    record_fingerprint(ctx.memory)
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.limits.max_steps = 30
    work = tmp / "work"
    work.mkdir(exist_ok=True)
    for n in FILES:
        (work / f"{n}.txt").write_text(
            f"File {n}. " + " ".join(f"{n}-{k} value {k * 7}" for k in range(items)),
            encoding="utf-8")
    return ctx


def _run(turns, window=None, local=None, setup=None, items=90, **mechanisms) -> dict:
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    here = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        os.chdir(tmp)
        ctx = _ctx(tmp, window, items, **mechanisms)
        try:
            if setup:
                setup(ctx)
            ctx.backends["cloud"]._responder = Script(turns)  # noqa: SLF001
            ctx.backends["local"]._responder = local or (  # noqa: SLF001
                lambda p, t: "summary: a file of numbered values")
            task = create_task(ctx.conn, ctx.log,
                               TaskContract(goal="g1 savings", artifact_scope=["work"]),
                               Budget(usd=5.0, seconds=600))
            status = run_task(ctx, task.id).status
            return _cost(ctx, task.id, status)
        finally:
            ctx.close()
            os.chdir(here)


def _cost(ctx, task_id, status) -> dict:
    rows = ctx.conn.execute(
        "SELECT backend, purpose, input_tokens, cache_read_tokens, "
        "cache_write_tokens, output_tokens, cost_usd FROM calls WHERE task_id=?",
        (task_id,)).fetchall()
    ex = [r for r in rows if r[0] == "cloud"]
    lo = [r for r in rows if r[0] != "cloud"]
    ev = lambda t: [json.loads(p) for (p,) in ctx.conn.execute(  # noqa: E731
        "SELECT payload_json FROM events WHERE task_id=? AND type=?", (task_id, t))]
    return {
        "status": status,
        "executor_calls": len(ex),
        "executor_prompt_tokens": sum((r[2] or 0) + (r[3] or 0) for r in ex),
        "executor_uncached_tokens": sum(r[2] or 0 for r in ex),
        "executor_cache_read_tokens": sum(r[3] or 0 for r in ex),
        "executor_cache_write_tokens": sum(r[4] or 0 for r in ex),
        "executor_usd_at_mock_prices": round(sum(r[6] or 0 for r in ex), 6),
        "local_calls": len(lo),
        "local_prompt_tokens": sum((r[2] or 0) + (r[3] or 0) for r in lo),
        "rotations": sum(1 for e in ev("session.opened") if e.get("parent_id")),
        "tombstones": max([e.get("tombstoned", 0) for e in ev("session.opened")] or [0]),
        "cache_hits": ctx.conn.execute(
            "SELECT COALESCE(SUM(hits),0) FROM semantic_cache").fetchone()[0],
        "prefix_fingerprints": len({e.get("prefix_fp") for e in ev("model.called")
                                    if e.get("prefix_fp")}),
        "run_code": sum(1 for e in ev("tool.receipt") if e.get("tool") == "run_code"),
    }


def _prefix_tokens() -> tuple[int, int]:
    from agent.tokens import count_tokens

    here = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        ctx = _ctx(Path(d))
        try:
            from agent.execute.loop import run_task
            from agent.kernel.tasks import create_task
            from agent.kernel.types import Budget, TaskContract

            ctx.backends["cloud"]._responder = Script([FINAL])  # noqa: SLF001
            t = create_task(ctx.conn, ctx.log,
                            TaskContract(goal="g1 savings", artifact_scope=["work"]),
                            Budget(usd=5.0, seconds=600))
            run_task(ctx, t.id)
            prefix = ctx.backends["cloud"].calls[0]["prefix"]
            file_tok = count_tokens((Path(d) / "work" / "a.txt").read_text(encoding="utf-8"))
            return count_tokens(prefix), file_tok
        finally:
            ctx.close()
            os.chdir(here)


def savings() -> dict:
    P, F = _prefix_tokens()
    out = {"prefix_tokens": P, "file_tokens": F, "mechanisms": {}}

    window = 16384
    items = int(90 * (0.28 * window - P) / 2.5 / F)
    reads = [READ(n) for n in FILES] + [FINAL]
    on = _run(reads, window=window, items=items)
    off = _run(reads, window=window, items=items, compaction=False)
    small = int((P + 2.5 * F) / 0.28)
    out["mechanisms"]["compaction"] = {
        "scenario": f"6 reads, files ~{int(F * items / 90)} tokens, window "
                    f"{window}: real ceiling crossed in the 3rd read. small: "
                    f"~{F}-token files, window {small} (prefix {P / small:.0%})",
        "on": on, "off": off,
        "small_on": _run(reads, window=small),
        "small_off": _run(reads, window=small, compaction=False)}

    def _compress(ctx):
        ctx.config.compress_observations_over_tokens = 50
    same = [READ("a")] * 3 + [FINAL]
    on = _run(same, setup=_compress)
    off = _run(same, setup=_compress, semantic_cache=False)
    out["mechanisms"]["semantic_cache"] = {
        "scenario": "the same file read 3 times, compressed on the local tier",
        "on": on, "off": off}

    mem = [READ("a"), REMEMBER("The deploy port is 8443."),
           READ("b"), READ("c"), READ("d"), FINAL]
    on, off = _run(mem), _run(mem, prefix_stability=False)
    mem3 = [READ("a"), REMEMBER("port 8443"), READ("b"), REMEMBER("host alpha"),
            READ("c"), REMEMBER("user ops"), READ("d"), FINAL]
    on3, off3 = _run(mem3), _run(mem3, prefix_stability=False)
    out["mechanisms"]["prefix_stability"] = {
        "scenario": "one memory write mid-task then 4 steps; and three writes",
        "on": on, "off": off, "on_3_writes": on3, "off_3_writes": off3}

    code = [CODE, FINAL]
    four = [READ(n) for n in "abcd"] + [FINAL]
    out["mechanisms"]["code_as_action"] = {
        "scenario": "fires: 4 files via one <code> block vs 4 reads. idle: 4 "
                    "reads with run_code offered vs not",
        "on": _run(code), "off": _run(four, code_as_action=False),
        "idle_on": _run(four), "idle_off": _run(four, code_as_action=False)}
    return out


EVIDENCE = ROOT / "docs" / "evidence"
RECORDS = [
    ("v3 2026-09-17 (answer in prompt)", EVIDENCE / "g1-ablation-2026-09-17.db",
     {a: EVIDENCE / f"g1-ablation-{a}.json" for a in
      ("control", "no-compaction", "no-semantic-cache", "no-prefix-stability",
       "no-code-as-action")}),
    ("v5 2026-09-23 (grader held out)", EVIDENCE / "g1-ablation-v5-2026-09-23.db",
     {a: EVIDENCE / f"g1-ablation-v5-{a}.json" for a in
      ("control", "no-compaction", "no-semantic-cache", "no-prefix-stability",
       "no-code-as-action")}),
]


def _replication_records():
    base = EVIDENCE / "g1-rep-v6"
    for d in sorted(base.glob("rep*-*")) if base.exists() else []:
        rep, arm = d.name.split("-", 1)
        db, js = d / "agent.g1.db", d / f"{arm}.json"
        if db.exists() and js.exists():
            yield (f"v6 {rep} (verifiers can fail)", db, {arm: js})


OFF = {"control": None, "no-compaction": "compaction",
       "no-semantic-cache": "semantic_cache", "no-prefix-stability": "prefix_stability",
       "no-code-as-action": "code_as_action"}


def _task_ids(js: Path) -> list[str]:
    body = json.loads(js.read_text(encoding="utf-8"))
    return [r["task_id"] for a in body.get("arms", {}).values() for r in a.get("results", [])]


def firerate() -> dict:
    tally = {m: {"steps": 0, "fired": 0} for m in
             ("compaction", "semantic_cache", "prefix_stability", "code_as_action")}
    detail = []
    for label, db, arms in [*RECORDS, *_replication_records()]:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for arm, js in arms.items():
                for tid in _task_ids(js):
                    ev = lambda t: [json.loads(p) for (p,) in conn.execute(  # noqa: E731,B023
                        "SELECT payload_json FROM events WHERE task_id=? AND type=?",
                        (tid, t))]
                    steps = sum(1 for e in ev("model.called") if e.get("purpose") == "step")
                    skipped = ev("compaction.skipped")
                    probes = sum(1 for e in skipped if e.get("where") == "probe")
                    rotations = sum(1 for e in ev("session.opened") if e.get("parent_id"))
                    boundary_skips = sum(1 for e in skipped
                                         if e.get("where") == "session_boundary")
                    receipts = [e.get("tool") for e in ev("tool.receipt")]
                    compress = conn.execute(
                        "SELECT COUNT(*) FROM calls WHERE task_id=? AND "
                        "purpose='compress_tool_output'", (tid,)).fetchone()[0]
                    fired = {
                        "compaction": max(0, rotations + boundary_skips - probes),
                        "semantic_cache": compress,
                        "prefix_stability": len(ev("memory.written")),
                        "code_as_action": receipts.count("run_code"),
                    }
                    row = {"record": label, "arm": arm, "task_id": tid,
                           "steps": steps, "probes": probes, **fired}
                    detail.append(row)
                    for m, n in fired.items():
                        if OFF[arm] == m:
                            continue
                        tally[m]["steps"] += steps
                        tally[m]["fired"] += n
        finally:
            conn.close()
    for m, t in tally.items():
        t["rate"] = t["fired"] / t["steps"] if t["steps"] else None
    return {"per_mechanism": tally, "tasks": len(detail), "detail": detail}


def _main(argv=None) -> int:
    sys.path.insert(0, str(ROOT / "src"))
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["savings", "firerate"])
    p.add_argument("--out", help="write the JSON here as well")
    args = p.parse_args(argv)
    result = savings() if args.what == "savings" else firerate()
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text if args.what == "savings" else json.dumps(
        {k: v for k, v in result.items() if k != "detail"}, indent=2))
    return 0


def main(argv=None) -> int:
    import sys
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'src'))
    from agent.awake import keep_awake

    with keep_awake():
        return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
