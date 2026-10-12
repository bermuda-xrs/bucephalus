from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent import HARNESS_VERSION  # noqa: E402
from measurement import open_measurement  # noqa: E402
from agent.execute.loop import ACTION_SCHEMA_HINT, run_task  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.ledger.prefix import SYSTEM_CONTRACT  # noqa: E402
from agent.proc import free_memory_gb, reap_orphans  # noqa: E402
from agent.workspace import reset_workspace  # noqa: E402

ENDPOINT = "http://127.0.0.1:11435/api/generate"

RUNG_STEPS = {"R4x": 1, "R4": 3, "R5": 10}
LOOP_RUNGS = tuple(RUNG_STEPS)
SEP = chr(10) * 2


class SuiteDataMissing(RuntimeError):
    ...


def load_bare_prompts(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise SuiteDataMissing(
            f"--bare-prompts file not found: {p}\n"
            "The ladder's text rungs (R0-R3) need the suite's bare-physics "
            "prompts. For the archived aerospace suite that file is "
            "bench/suites/archive/aero_calc.bare.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    for field in ("output_contract", "step_by_step", "bare"):
        if field not in data:
            raise SuiteDataMissing(f"{p} is missing required field {field!r}")
    return data


def bare_for(data: dict, key: str) -> str:
    bare = data.get("bare") or {}
    if key not in bare:
        raise SuiteDataMissing(
            f"no bare-physics prompt for task {key!r}. "
            f"Available: {', '.join(sorted(bare)) or '(none)'}. "
            "Every rung must be answerable from its own prompt, so a task "
            "without one cannot be laddered at R0-R3.")
    return bare[key]


def complete(model: str, prompt: str, num_ctx: int = 16384,
             num_predict: int = 512) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.0, "num_ctx": num_ctx,
                                   "num_predict": num_predict}}).encode()
    req = urllib.request.Request(ENDPOINT, body,
                                 {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=1800).read())["response"]


_NUM = re.compile(r"-?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")


def numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text.replace("\\,", "").replace(",", "")):
        try:
            out.append(float(m))
        except ValueError:
            continue
    return out


def score_text(text: str, expected: float, tol: float,
               out_key: str | None = None) -> dict:
    ns = numbers(text)
    hits = [n for n in ns if expected and abs(n - expected) / abs(expected) <= tol]
    last = ns[-1] if ns else None
    correct_last = bool(last is not None and expected
                        and abs(last - expected) / abs(expected) <= tol)

    labelled = None
    if out_key:
        m = re.findall(re.escape(out_key) + r"[^0-9\-]{0,20}(-?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?)",
                       text)
        if m:
            try:
                labelled = float(m[-1].replace(",", ""))
            except ValueError:
                labelled = None

    answer = labelled if labelled is not None else last
    correct = bool(answer is not None and expected
                   and abs(answer - expected) / abs(expected) <= tol)
    return {"correct": correct, "answer": answer,
            "correct_last_number": correct_last, "last_number": last,
            "labelled_answer": labelled, "hits": hits,
            "numbers_seen": ns[:40], "n_numbers": len(ns)}


def goal_text(ctx, key: str) -> str:
    row = ctx.conn.execute(
        "SELECT goal FROM suite_tasks WHERE task_key=?", (key,)).fetchone()
    return row[0] if row else ""


def expected_for(key: str, grading_dir: str | Path = "grading") -> tuple[str, float, float]:
    g = Path(grading_dir) / key / "verify.py"
    if not g.exists():
        raise SuiteDataMissing(
            f"no grader at {g}. The ladder scores against the suite's own "
            "verify.py; pass --grading-dir if it is not at ./grading")
    src = g.read_text(encoding="utf-8")
    exp = eval(re.search(r"EXPECTED = (\{.*?\})\n", src).group(1))
    tol = float(re.search(r"TOL = ([\d.e-]+)", src).group(1))
    k = list(exp)[0]
    return k, float(exp[k]), tol


def run_rung(ctx, rung: str, key: str, model: str, samples: int,
             step_by_step: bool = False, data: dict | None = None,
             suite: str = "", grading_dir: str | Path = "grading") -> list[dict]:
    out_key, expected, tol = expected_for(key, grading_dir)
    out = []

    for i in range(samples):
        t0 = time.time()
        if rung not in LOOP_RUNGS:
            bare = bare_for(data, key)
            contract = data["output_contract"].format(key=key, out_key=out_key)
            if step_by_step:
                contract = contract + " " + data["step_by_step"]
            if rung == "R0":
                prompt = bare
            elif rung == "R1":
                prompt = bare + SEP + contract
            elif rung == "R2":
                prompt = SYSTEM_CONTRACT + SEP + bare + SEP + contract
            else:
                prompt = (SYSTEM_CONTRACT + SEP + ctx.tools.manual()
                          + SEP + bare + SEP + contract
                          + chr(10) + ACTION_SCHEMA_HINT)
            text = complete(model, prompt)
            sc = score_text(text, expected, tol, out_key)
            row = {"rung": rung, "sample": i + 1,
                   "wall_s": round(time.time() - t0, 1),
                   "chars": len(text), "prompt_chars": len(prompt),
                   "response": text}
            row.update(sc)
            out.append(row)
        else:
            spec = ctx.userdata.get_suite_by_name(suite).to_specs()
            spec = next(s for s in spec if s["key"] == key)
            if step_by_step:
                spec = dict(spec)
                spec["goal"] = spec["goal"] + " " + data["step_by_step"]
            reset_workspace(ctx.config.root, spec["artifact_scope"],
                            spec.get("fixtures") or {})
            ctx.config.limits.max_steps = RUNG_STEPS[rung]
            task = create_task(ctx.conn, ctx.log, TaskContract(
                goal=spec["goal"], success_criterion="verify_program",
                output_schema=spec.get("output_schema", {}),
                artifact_scope=spec["artifact_scope"],
                grader_dir=spec.get("grader_dir")),
                Budget(usd=0.0, seconds=spec.get("budget_seconds") or 600))
            ctx.set_frozen_skills(task.id, [])
            res = run_task(ctx, task.id)
            f = Path(ctx.config.root) / spec["artifact_scope"][0] / "output" / "answer.json"
            got, raw_artifact = None, None
            if f.exists():
                try:
                    raw_artifact = f.read_text(encoding="utf-8")[:2000]
                except OSError:
                    raw_artifact = None
                try:
                    got = float(list(json.loads(raw_artifact).values())[0])
                except Exception:  # noqa: BLE001
                    got = None
            rel = (abs(got - expected) / abs(expected)
                   if (got is not None and expected) else None)
            out.append({"rung": rung, "sample": i + 1,
                        "correct": res.status in ("done", "done_unannounced"),
                        "answer": got, "status": res.status,
                        "max_steps": RUNG_STEPS[rung],
                        "relative_error": rel,
                        "answer_json_raw": raw_artifact,
                        "goal_chars": len(spec["goal"]),
                        "wall_s": round(time.time() - t0, 1)})
        r = out[-1]
        print(f"  {rung} {i+1}/{samples}  correct={r['correct']}  "
              f"answer={r['answer']}  {r['wall_s']}s", flush=True)
    return out


def _main() -> int:
    ap = argparse.ArgumentParser(description="the scaffold ladder")
    ap.add_argument("--config", default="config.local.yaml")
    ap.add_argument("--task", required=True,
                    help="task key to ladder. REQUIRED - no default")
    ap.add_argument("--suite", required=True,
                    help="suite name for the R4/R5 loop rungs. "
                         "REQUIRED - was hard-coded to aero_calc")
    ap.add_argument("--bare-prompts", required=True,
                    help="JSON of the suite's bare-physics prompts and "
                         "output contract. For the archived aerospace "
                         "suite: bench/suites/archive/aero_calc.bare.json")
    ap.add_argument("--grading-dir", default="grading",
                    help="where verify.py lives, per task key")
    ap.add_argument("--model", default="granite4.1:3b")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--rungs", default="R0,R1,R2,R3,R4,R5",
                    help="R4 is now max_steps=3; the old one-action probe is "
                         "R4x and is not a correctness measurement")
    ap.add_argument("--step-by-step", action="store_true",
                    help="add the v3 reasoning instruction to every rung")
    ap.add_argument("--out", default="runs/ladder.json")
    args = ap.parse_args()

    reaped = reap_orphans()
    print(f"reaped {len(reaped)} orphan(s); free {free_memory_gb():.1f} GB\n")

    ctx = open_measurement(args.config, profile="local", isolate=False,
                           why="scaffolding rungs on single tasks: no arms, "
                               "no retrieval")
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.backends["cloud"].model = args.model
    from agent.models.registry import build_backends

    ctx.backends = build_backends(ctx.config)

    data = load_bare_prompts(args.bare_prompts)
    bare_for(data, args.task)
    k, expected, tol = expected_for(args.task, args.grading_dir)
    print(f"{args.task}: {k} = {expected} (tol {tol})  model={args.model}  step_by_step={args.step_by_step}\n")

    rows = []
    for rung in args.rungs.split(","):
        rows += run_rung(ctx, rung.strip(), args.task, args.model,
                         args.samples, args.step_by_step, data,
                         args.suite, args.grading_dir)
        print()

    dest = Path(ctx.config.resolve(args.out))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(
        {"task": args.task, "model": args.model, "expected": expected,
         "tolerance": tol, "samples": args.samples,
         "harness_version": HARNESS_VERSION,
         "step_by_step": args.step_by_step,
         "step_by_step_text": data["step_by_step"] if args.step_by_step else None,
         "suite": args.suite, "bare_prompts": args.bare_prompts,
         "rungs": args.rungs, "rows": rows},
        indent=2), encoding="utf-8")

    print(f"{'rung':<5} {'correct':>9}  answers")
    for rung in args.rungs.split(","):
        rung = rung.strip()
        rr = [r for r in rows if r["rung"] == rung]
        n = sum(1 for r in rr if r["correct"])
        ans = sorted({str(r["answer"]) for r in rr})
        print(f"{rung:<5} {n}/{len(rr):>7}  {', '.join(ans)[:70]}")
    print(f"\nwritten to {dest}")
    ctx.close()
    return 0


def main(argv=None) -> int:
    import sys
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'src'))
    from agent.awake import keep_awake

    with keep_awake():
        return _main()


if __name__ == "__main__":
    raise SystemExit(main())
