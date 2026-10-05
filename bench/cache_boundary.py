from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bench"))

import agent.execute.loop as loop  # noqa: E402
from agent.config import load_config  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.kernel import sessions  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.memory.store import MemoryStore  # noqa: E402
from measurement import assert_isolation_declared  # noqa: E402

PREDICTION = ROOT / "docs" / "evidence" / "phase9-cache-boundary-PREDICTION.json"
OUT = ROOT / "docs" / "evidence" / "phase9-cache-boundary.json"
T = 64
FACT = "The deploy port for this service is 8443."
FINAL = '<final>{"answer": "done"}</final>'
GOAL = "Read the five files in work/ and report what they contain."
WORK = {f"{n}.txt": f"File {n}. " + " ".join(
    f"{n}-{k} value {k * 7}" for k in range(90)) for n in "abcde"}


def read(name):
    return '<action tool="read_file">' + json.dumps({"path": f"work/{name}"}) + "</action>"


def remember(text):
    return '<action tool="memory_write">' + json.dumps({"text": text}) + "</action>"


class Measured:
    def __init__(self, real, script):
        self.real, self.script, self.rows = real, list(script), []

    def __getattr__(self, name):
        return getattr(self.real, name)

    def complete(self, prefix, tail, tools=None, max_tokens=4096,
                 temperature=0.0, schema=None, on_chunk=None):
        r = self.real.complete(prefix, tail, tools, max_tokens=1,
                               temperature=0.0, schema=schema)
        self.rows.append({
            "flush": "about to be compacted" in tail,
            "prefix_sha256": hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:16],
            "prompt": r.usage.input_tokens,
            "cached": r.usage.cache_read_tokens,
            "wall_ms": r.wall_ms,
            "_prefix": prefix,
        })
        text = self.script.pop(0)
        try:
            return r.model_copy(update={"text": text})
        except AttributeError:
            return dataclasses.replace(r, text=text)


def fire_once(n):
    seen = {"k": 0}

    def fake(prefix_tokens, tail_tokens, window, ratio):
        seen["k"] += 1
        return "compact" if seen["k"] == n else "ok"
    return fake


def committed_prediction() -> str:
    def git(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                               text=True).stdout.strip()
    rel = str(PREDICTION.relative_to(ROOT))
    if git("status", "--porcelain", "--", rel):
        raise SystemExit("the prediction has uncommitted edits: a prediction "
                         "revised after measuring is not a prediction")
    commit = git("log", "-1", "--format=%h %cI", "--", rel)
    if not commit:
        raise SystemExit("the prediction is not committed; state it first")
    return commit


def server_version(base_url: str) -> str:
    root = base_url.rsplit("/v1", 1)[0]
    with urllib.request.urlopen(root + "/api/version", timeout=10) as r:
        return json.loads(r.read())["version"]


def run_condition(cfg_path: Path, label: str, flush_reply: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix=f"p9-{label}-"))
    cfg = load_config(cfg_path)
    cfg.root = str(tmp)
    cfg.db_path = str(tmp / "agent.db")
    cfg.anchor_path = str(tmp / "anchor" / "chain.anchor")
    cfg.skills_path = str(tmp / "skills")
    cfg.isolate_retrieval = False
    cfg.autonomy.auto_review = False
    cfg.autonomy.approve_scoped_writes = True
    (tmp / "work").mkdir()
    for name, text in WORK.items():
        (tmp / "work" / name).write_text(text, encoding="utf-8")

    ctx = AgentContext(cfg)
    assert_isolation_declared(ctx)
    ctx.memory = MemoryStore(tmp / "memory")
    real = ctx.backends["cloud"]
    real.cfg.usable_context_window = 3000
    m = Measured(real, [read("a.txt"), read("b.txt"), read("c.txt"),
                        read("d.txt"), flush_reply, read("e.txt"), FINAL])
    ctx.backends["cloud"] = m
    loop.enforce_ceiling = fire_once(5)

    real.complete("You are a calculator. Reply with digits only.", "17 * 3 =",
                  None, max_tokens=1, temperature=0.0)

    task = create_task(ctx.conn, ctx.log,
                       TaskContract(goal=GOAL, artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=3600))
    t0 = time.time()
    result = loop.run_task(ctx, task.id)
    opened = [json.loads(p) for (p,) in ctx.conn.execute(
        """SELECT payload_json FROM events WHERE task_id=? AND type='session.opened'
           ORDER BY seq""", (task.id,))]
    return {"ctx": ctx, "real": real, "rows": m.rows, "status": result.status,
            "wall_s": round(time.time() - t0, 1),
            "sessions": len(sessions.for_task(ctx.conn, task.id)),
            "boundary_event": opened[-1] if len(opened) > 1 else None}


def split(rows):
    i = next(k for k, r in enumerate(rows) if r["flush"])
    return rows[:i], rows[i], rows[i + 1:]


def evaluate(A: list, B: list, refs: dict) -> dict:
    preA, _, postA = split(A)
    preB, _, postB = split(B)
    H = refs["H"]

    def pairs(seq):
        return [{"prev_prompt": a["prompt"], "cached": b["cached"],
                 "floor": a["prompt"] - (H + T),
                 "ok": b["cached"] >= a["prompt"] - (H + T)}
                for a, b in zip(seq, seq[1:])]

    p1, p4 = pairs(preA) + pairs(preB), pairs(postA) + pairs(postB)
    bA, bB = postA[0], postB[0]
    return {
        "P1_within_a_session": {"ok": all(x["ok"] for x in p1), "pairs": p1},
        "P2_boundary_memory_unchanged": {
            "cached_first_after": bB["cached"],
            "ref_prefix": refs["ref_prefix"],
            "cached_last_step_before": preB[-1]["cached"],
            "ok": refs["ref_prefix"] - T <= bB["cached"] < preB[-1]["cached"]},
        "P3_boundary_memory_changed": {
            "cached_first_after": bA["cached"],
            "ref_first_diff": refs["ref_first_diff"],
            "cached_first_after_B": bB["cached"],
            "ok": (abs(bA["cached"] - refs["ref_first_diff"]) <= T
                   and bA["cached"] < bB["cached"] - T)},
        "P4_recovery": {"ok": all(x["ok"] for x in p4), "pairs": p4},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.openai.yaml"))
    args = ap.parse_args()
    commit = committed_prediction()

    A = run_condition(Path(args.config), "A", remember(FACT))
    B = run_condition(Path(args.config), "B", "NOTHING")

    real, tools = A["real"], A["ctx"].tools.schemas()
    pre = next(r["_prefix"] for r in A["rows"] if not r["flush"])
    post = split(A["rows"])[2][0]["_prefix"]
    diff = next((i for i, (x, y) in enumerate(zip(pre, post)) if x != y),
                min(len(pre), len(post)))

    def pt(system, user):
        return real.complete(system, user, tools, max_tokens=1,
                             temperature=0.0).usage.input_tokens

    tail_x, _ = loop._envelope(real, "X")
    refs = {"ref_prefix": pt(pre, ""), "ref_first_diff": pt(pre[:diff], ""),
            "H": pt(pre, tail_x) - pt(pre, "X"),
            "first_diff_char": diff, "memory_block_char": pre.find("<memory"),
            "prefix_chars": len(pre)}
    ev = evaluate(A["rows"], B["rows"], refs)
    failed = [k for k, v in ev.items() if not v["ok"]]

    def public(c):
        return {"status": c["status"], "wall_s": c["wall_s"],
                "sessions": c["sessions"], "boundary_event": c["boundary_event"],
                "rows": [{k: v for k, v in r.items() if k != "_prefix"}
                         for r in c["rows"]]}

    report = {
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "prediction": str(PREDICTION.relative_to(ROOT)).replace("\\", "/"),
        "prediction_commit": commit,
        "prediction_sha256": hashlib.sha256(PREDICTION.read_bytes()).hexdigest(),
        "server": f"Ollama {server_version(real.cfg.resolved_base_url)}",
        "model": real.cfg.model,
        "method": ("decisions scripted, prefill real (max_tokens=1 per prompt "
                   "the loop built); temporary db and memory per condition; an "
                   "eviction prompt before each; references measured last"),
        "T": T,
        "B_prefix_unchanged_at_boundary": split(B["rows"])[2][0]["_prefix"] == pre,
        "A_prefix_changed_at_boundary": post != pre,
        "conditions": {"A_memory_changed": public(A),
                       "B_memory_unchanged": public(B)},
        "references": refs,
        "evaluation": ev,
        "verdict": ("every prediction held" if not failed
                    else "FAILED: " + ", ".join(failed)),
    }
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "server", "verdict", "A_prefix_changed_at_boundary",
        "B_prefix_unchanged_at_boundary", "references")}, indent=2))
    for k, v in ev.items():
        print(f"{k:32s} {'held' if v['ok'] else 'FAILED'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
