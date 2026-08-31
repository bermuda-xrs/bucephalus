"""G1 — where does the scaffold stop the model getting the answer right?

Measured, and the reason this exists:

    granite4.1:3b, E01, asked DIRECTLY      7668 vs 7672.6   5/5 correct
    granite4.1:3b, E01, full agent loop     11185784         5/5 wrong

Same model, same question, deterministic in both directions. Not noise, not
task difficulty: the scaffold degrades a model that can do the task. This
walks the scaffold up one rung at a time until correctness collapses, so the
collapse can be attributed to a specific addition rather than to "the agent".

    R0  bare prompt, plain physics question
    R1  R0 + the suite's own goal text (paths, key names, "use run_code")
    R2  R1 + the SYSTEM_CONTRACT prefix the agent always sends
    R3  R2 + the tool manual, still one turn, still no tools callable
    R4  the real loop, max_steps=1
    R5  the real loop, unrestricted

R0-R3 are single completions scored on the number in the text. R4-R5 run the
real loop and are scored by the real grader on the artifact. That is a
deliberate seam: R0-R3 ask "can it compute this", R4-R5 ask "can it deliver
this", and the gap between R3 and R4 is itself informative.

  python bench/ladder.py --task E01 --samples 5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import ACTION_SCHEMA_HINT, run_task  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget, TaskContract  # noqa: E402
from agent.ledger.prefix import SYSTEM_CONTRACT  # noqa: E402
from agent.proc import free_memory_gb, reap_orphans  # noqa: E402
from agent.workspace import reset_workspace  # noqa: E402

ENDPOINT = "http://127.0.0.1:11435/api/generate"
SEP = chr(10) * 2          # blank line between prompt sections

# The I/O contract the suite imposes, minus the parts that need a file
# system or a tool. This is the scaffolding whose cost the ladder measures.
OUTPUT_CONTRACT = (
    "Write the answer to work/{key}/output/answer.json as a JSON object "
    "containing exactly this key, spelled exactly as shown: {out_key}. "
    "Values in SI base units unless the key name says otherwise. "
    "State the final number in your reply as well."
)

# R0: the physics, with nothing else. This is the D1 prompt.
BARE = ("Earth has gravitational parameter mu = 3.986004418e14 m^3/s^2 and "
        "radius 6371000 m. A satellite is in a circular orbit at an altitude "
        "of 400 km. Compute its circular orbital velocity in m/s. Give the "
        "final number.")

BARE_BY_TASK = {
    "E01": BARE,
    "E03": ("Mars has gravitational parameter mu = 4.282837e13 m^3/s^2 and "
            "radius 3389500 m. Compute the escape velocity at its surface in "
            "m/s. Give the final number."),
    "E04": ("A rocket stage has specific impulse 311 s, initial mass 549054 kg "
            "and final mass 137000 kg. Standard gravity is 9.80665 m/s^2. "
            "Compute the ideal delta-v in m/s by the Tsiolkovsky equation. "
            "Give the final number."),
    "E05": ("Air density is 0.4135 kg/m^3 and speed is 900 km/h. Compute the "
            "dynamic pressure in pascals. Give the final number."),
}


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


def score_text(text: str, expected: float, tol: float) -> tuple[bool, float | None]:
    """(correct, the number judged to be its answer).

    The last in-range number is taken as the answer, which is where a worked
    solution puts it. `any_hit` is reported separately so a model that
    computed the right value and then mangled it is not scored the same as
    one that never had it.
    """
    ns = numbers(text)
    hits = [n for n in ns if expected and abs(n - expected) / abs(expected) <= tol]
    last = ns[-1] if ns else None
    correct = bool(last is not None and expected
                   and abs(last - expected) / abs(expected) <= tol)
    return correct, (last if correct or not hits else hits[-1])


def goal_text(ctx, key: str) -> str:
    row = ctx.conn.execute(
        "SELECT goal FROM suite_tasks WHERE task_key=?", (key,)).fetchone()
    return row[0] if row else ""


def expected_for(key: str) -> tuple[str, float, float]:
    g = Path("grading") / key / "verify.py"
    src = g.read_text(encoding="utf-8")
    exp = eval(re.search(r"EXPECTED = (\{.*?\})\n", src).group(1))
    tol = float(re.search(r"TOL = ([\d.e-]+)", src).group(1))
    k = list(exp)[0]
    return k, float(exp[k]), tol


def run_rung(ctx, rung: str, key: str, model: str, samples: int) -> list[dict]:
    """One rung, `samples` times. R4/R5 use the real loop and real grader."""
    out_key, expected, tol = expected_for(key)
    goal = goal_text(ctx, key)
    out = []

    for i in range(samples):
        t0 = time.time()
        if rung in ("R0", "R1", "R2", "R3"):
            # EVERY rung must be ANSWERABLE from its own prompt. The first
            # version of R1 was the suite goal verbatim, which says "read
            # work/E01/input/params.json" and "use run_code" - at R1 there is
            # no file access and no tool, so the model could not know mu,
            # radius or altitude and the rung was unanswerable. A failure
            # there would have measured my prompt, not the scaffold.
            #
            # So the physics VALUES stay inline at every text rung, and what
            # escalates is the scaffolding around them.
            bare = BARE_BY_TASK.get(key, BARE)
            contract = OUTPUT_CONTRACT.format(key=key, out_key=out_key)
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
            ok, got = score_text(text, expected, tol)
            out.append({"rung": rung, "sample": i + 1, "correct": ok,
                        "answer": got, "wall_s": round(time.time() - t0, 1),
                        "chars": len(text), "prompt_chars": len(prompt),
                        "response": text[:1500]})
        else:
            spec = ctx.userdata.get_suite_by_name("aero_calc").to_specs()
            spec = next(s for s in spec if s["key"] == key)
            reset_workspace(ctx.config.root, spec["artifact_scope"],
                            spec.get("fixtures") or {})
            ctx.config.limits.max_steps = 1 if rung == "R4" else 10
            task = create_task(ctx.conn, ctx.log, TaskContract(
                goal=spec["goal"], success_criterion="verify_program",
                output_schema=spec.get("output_schema", {}),
                artifact_scope=spec["artifact_scope"],
                grader_dir=spec.get("grader_dir")),
                Budget(usd=0.0, seconds=spec.get("budget_seconds") or 600))
            ctx.set_frozen_skills(task.id, [])
            res = run_task(ctx, task.id)
            f = Path(ctx.config.root) / spec["artifact_scope"][0] / "output" / "answer.json"
            got = None
            if f.exists():
                try:
                    got = float(list(json.loads(f.read_text(encoding="utf-8")).values())[0])
                except Exception:  # noqa: BLE001
                    got = None
            out.append({"rung": rung, "sample": i + 1,
                        "correct": res.status in ("done", "done_unannounced"),
                        "answer": got, "status": res.status,
                        "wall_s": round(time.time() - t0, 1)})
        r = out[-1]
        print(f"  {rung} {i+1}/{samples}  correct={r['correct']}  "
              f"answer={r['answer']}  {r['wall_s']}s", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="the scaffold ladder")
    ap.add_argument("--config", default="config.local.yaml")
    ap.add_argument("--task", default="E01")
    ap.add_argument("--model", default="granite4.1:3b")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--rungs", default="R0,R1,R2,R3,R4,R5")
    ap.add_argument("--out", default="runs/ladder.json")
    args = ap.parse_args()

    reaped = reap_orphans()
    print(f"reaped {len(reaped)} orphan(s); free {free_memory_gb():.1f} GB\n")

    ctx = AgentContext.open(args.config, profile="local")
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.backends["cloud"].model = args.model
    from agent.models.registry import build_backends

    ctx.backends = build_backends(ctx.config)

    k, expected, tol = expected_for(args.task)
    print(f"{args.task}: {k} = {expected} (tol {tol})  model={args.model}\n")

    rows = []
    for rung in args.rungs.split(","):
        rows += run_rung(ctx, rung.strip(), args.task, args.model, args.samples)
        print()

    dest = Path(ctx.config.resolve(args.out))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(
        {"task": args.task, "model": args.model, "expected": expected,
         "tolerance": tol, "samples": args.samples, "rows": rows},
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


if __name__ == "__main__":
    raise SystemExit(main())
