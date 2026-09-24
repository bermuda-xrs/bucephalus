"""G1 closed as the product of two measured quantities (FINDINGS 1.35).

Four live runs said the same thing: on granite4.1:3b three of the four
mechanisms never engage, because the model rarely reads anything (1.32). A
fifth task redesign would not change a property of the model. So G1 is
stated per mechanism as

    mechanism X saves N tokens WHEN IT FIRES, and fires in F% of steps

with each factor measured where it can be measured without noise:

  savings   A SCRIPTED model and controlled observations, each mechanism on
            and off over byte-identical work. No live model, nothing
            nondeterministic: run it twice and every number repeats.
  firerate  Read straight out of the recorded real-model runs: how often
            each mechanism actually engaged on the 3B, per executor step.

    python bench/g1_close.py savings  [--out docs/evidence/g1-close-savings.json]
    python bench/g1_close.py firerate [--out docs/evidence/g1-close-firerate.json]

The savings side is a MODEL of the provider, and says so. The mock's prefix
cache is exact longest-prefix on the prefix bytes, which is Anthropic's
behaviour; this project's Ollama server reuses less than that (FINDINGS
1.21's cache-reuse probe found it snapping back to checkpoints), so a prefix
saving here is an upper bound on what the local server gives back.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# savings: scripted, deterministic
# ---------------------------------------------------------------------------

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
    """The model's side, as a fixed list of turns.

    A responder, not the backend's queue: compaction's pre-boundary memory
    flush is an extra model call, and from a queue it would consume the next
    scripted turn - so the arm WITH compaction would do different work from
    the arm without it. The flush is answered here and the work stays
    byte-identical across arms. The flush call itself is still counted: it is
    part of what compaction costs.
    """

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
    # The local tier on the SAME backend as the executor, as in the measured
    # runs (config.local.yaml, v5): the escalation guard permits that, so the
    # cache arm needs no test-only bypass.
    cfg.backends["local"] = cfg.backends["cloud"].model_copy(
        update={"trust_tier": "local"})
    if window:
        cfg.backends["cloud"].usable_context_window = window
    ctx = AgentContext(cfg)
    ctx.memory = MemoryStore(tmp / "memory")
    record_fingerprint(ctx.memory)
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.limits.max_steps = 30      # every scenario runs to its <final>
    work = tmp / "work"
    work.mkdir(exist_ok=True)
    for n in FILES:
        (work / f"{n}.txt").write_text(
            f"File {n}. " + " ".join(f"{n}-{k} value {k * 7}" for k in range(items)),
            encoding="utf-8")
    return ctx


def _run(turns, window=None, local=None, setup=None, items=90, **mechanisms) -> dict:
    """One scripted task in a fresh directory. Returns what it cost."""
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    here = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        os.chdir(tmp)                 # the sandbox resolves scope from cwd
        ctx = _ctx(tmp, window, items, **mechanisms)
        try:
            if setup:
                setup(ctx)
            ctx.backends["cloud"]._responder = Script(turns)          # noqa: SLF001
            ctx.backends["local"]._responder = local or (             # noqa: SLF001
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
        # What was SENT, cached or not: prefix + tail on every call.
        "executor_prompt_tokens": sum((r[2] or 0) + (r[3] or 0) for r in ex),
        # What had to be PREFILLED: the mock serves a byte-identical prefix
        # from cache, so this is the tail plus any prefix that moved.
        "executor_uncached_tokens": sum(r[2] or 0 for r in ex),
        "executor_cache_read_tokens": sum(r[3] or 0 for r in ex),
        "executor_cache_write_tokens": sum(r[4] or 0 for r in ex),
        "executor_usd_at_mock_prices": round(sum(r[6] or 0 for r in ex), 6),
        "local_calls": len(lo),
        "local_prompt_tokens": sum((r[2] or 0) + (r[3] or 0) for r in lo),
        "rotations": sum(1 for e in ev("session.opened") if e.get("parent_id")),
        # Tombstones in the tail at the last boundary (the rotation record).
        "tombstones": max([e.get("tombstoned", 0) for e in ev("session.opened")] or [0]),
        "cache_hits": ctx.conn.execute(
            "SELECT COALESCE(SUM(hits),0) FROM semantic_cache").fetchone()[0],
        "prefix_fingerprints": len({e.get("prefix_fp") for e in ev("model.called")
                                    if e.get("prefix_fp")}),
        "run_code": sum(1 for e in ev("tool.receipt") if e.get("tool") == "run_code"),
    }


def _prefix_tokens() -> tuple[int, int]:
    """How big the prefix is, so the window can be sized to cross the
    ceiling on the tail rather than on the prefix (which is 'delegate', a
    different branch)."""
    from agent.tokens import count_tokens

    here = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        ctx = _ctx(Path(d))
        try:
            from agent.execute.loop import run_task
            from agent.kernel.tasks import create_task
            from agent.kernel.types import Budget, TaskContract

            ctx.backends["cloud"]._responder = Script([FINAL])       # noqa: SLF001
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

    # COMPACTION. The REAL ceiling, no probe: the shipped executor window
    # (16384, ceiling 0.28 of it) with files sized so it is crossed during
    # the third read, then three more reads. And the geometry that exposed
    # the target defect: a small window, the prefix over 8% of it.
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

    # SEMANTIC CACHE. The same large file read three times; observations over
    # 50 tokens are compressed on the local tier. The saving is local-tier
    # calls, NOT executor tokens: the executor sees the same compressed text
    # either way.
    def _compress(ctx):
        ctx.config.compress_observations_over_tokens = 50
    same = [READ("a")] * 3 + [FINAL]
    on = _run(same, setup=_compress)
    off = _run(same, setup=_compress, semantic_cache=False)
    out["mechanisms"]["semantic_cache"] = {
        "scenario": "the same file read 3 times, compressed on the local tier",
        "on": on, "off": off}

    # PREFIX STABILITY. One tier-1 write mid-task, then four more steps.
    mem = [READ("a"), REMEMBER("The deploy port is 8443."),
           READ("b"), READ("c"), READ("d"), FINAL]
    on, off = _run(mem), _run(mem, prefix_stability=False)
    mem3 = [READ("a"), REMEMBER("port 8443"), READ("b"), REMEMBER("host alpha"),
            READ("c"), REMEMBER("user ops"), READ("d"), FINAL]
    on3, off3 = _run(mem3), _run(mem3, prefix_stability=False)
    out["mechanisms"]["prefix_stability"] = {
        "scenario": "one memory write mid-task then 4 steps; and three writes",
        "on": on, "off": off, "on_3_writes": on3, "off_3_writes": off3}

    # CODE AS ACTION. Fires: the same four files summed, once as one <code>
    # block, once as four reads (the only way the ablated arm can do it).
    # Idle: four reads under both arms, so the difference is what carrying
    # run_code and its instructions costs a task that never uses them.
    code = [CODE, FINAL]
    four = [READ(n) for n in "abcd"] + [FINAL]
    out["mechanisms"]["code_as_action"] = {
        "scenario": "fires: 4 files via one <code> block vs 4 reads. idle: 4 "
                    "reads with run_code offered vs not",
        "on": _run(code), "off": _run(four, code_as_action=False),
        "idle_on": _run(four), "idle_off": _run(four, code_as_action=False)}
    return out


# ---------------------------------------------------------------------------
# firerate: the recorded real-model runs
# ---------------------------------------------------------------------------

# (label, database, {arm: arm-json}) - every real-model G1 run kept.
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
    """The v6 replication, one database per repetition and arm."""
    base = EVIDENCE / "g1-rep-v6"   # copied from runs/, which is gitignored
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
                        # A REAL ceiling crossing: every boundary, rotated or
                        # skipped, minus the ones the probe forced.
                        "compaction": max(0, rotations + boundary_skips - probes),
                        "semantic_cache": compress,
                        "prefix_stability": len(ev("memory.written")),
                        "code_as_action": receipts.count("run_code"),
                    }
                    row = {"record": label, "arm": arm, "task_id": tid,
                           "steps": steps, "probes": probes, **fired}
                    detail.append(row)
                    for m, n in fired.items():
                        # A mechanism cannot fire in the arm that removed it;
                        # counting those steps would dilute F with zeros the
                        # arm guaranteed.
                        if OFF[arm] == m:
                            continue
                        tally[m]["steps"] += steps
                        tally[m]["fired"] += n
        finally:
            conn.close()
    for m, t in tally.items():
        t["rate"] = t["fired"] / t["steps"] if t["steps"] else None
    return {"per_mechanism": tally, "tasks": len(detail), "detail": detail}


def main(argv=None) -> int:
    sys.path.insert(0, str(ROOT / "src"))
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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


if __name__ == "__main__":
    raise SystemExit(main())
