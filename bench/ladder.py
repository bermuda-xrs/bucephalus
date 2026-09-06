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
    R4  the real loop, minimum viable step budget (3)
    R5  the real loop, unrestricted (10)
    R4x the real loop with ONE action allowed - a step-budget probe, NOT a
        correctness measurement. See below.

R4 USED TO MEAN max_steps=1 AND ITS PUBLISHED RESULT IS VOID.

    A one-action budget cannot measure "can it deliver this". It measures
    "did the model happen to spend its single action on run_code". Measured
    on 2026-09-03: of five samples, one spent its step on run_code and wrote
    an answer; four spent it on read_file(params.json) - correct agent
    behaviour, reading your input before computing - and produced no artifact
    at all, in 7.4 s each.

    The bias is directional, which is what makes it dangerous rather than
    merely noisy: "work through it step by step" is an instruction to take
    preparatory steps, so a one-action budget systematically penalises the
    intervention it was being used to test.

    R4 is now max_steps=3, the minimum that can cover read, compute and
    finalise. The one-action probe survives as R4x, named for what it is.

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

# Step budget per loop rung. Recorded into every row so a rung's meaning is
# readable from the data alone rather than from this file's history - the old
# R4 was published as "the full loop, one step allowed" and read as a
# correctness result for months.
RUNG_STEPS = {"R4x": 1, "R4": 3, "R5": 10}
LOOP_RUNGS = tuple(RUNG_STEPS)
SEP = chr(10) * 2          # blank line between prompt sections

# The ladder no longer carries any suite's physics in its source.
#
# OUTPUT_CONTRACT, STEP_BY_STEP and the per-task bare prompts are now DATA,
# supplied by --bare-prompts alongside the suite they belong to. They lived
# here as literals for the aerospace suite's whole life, which is how a
# general instrument came to hard-code one domain's orbital mechanics.
#
# The values are unchanged. They were extracted programmatically rather than
# retyped, and tests/test_ladder_decoupled.py pins the resulting prompt bytes
# against the pre-quarantine hashes.


class SuiteDataMissing(RuntimeError):
    """Raised when the ladder is asked for something the data does not carry.

    Never falls back to a default task's physics. A silent fallback would
    measure the wrong question and report it as the right one - the failure
    mode this whole instrument exists to detect.
    """


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
    """The bare physics question for `key`. RAISES if absent - never guesses.

    The previous version fell back to E01's orbital-mechanics text for any
    unknown key, so asking for a task the data did not cover silently
    measured a different question entirely.
    """
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
    """What the model answered, how it was judged, and everything discarded.

    Returns a dict rather than a pair so the run record keeps the evidence:
    `numbers_seen` and `hits` make every verdict re-checkable without the
    prose, which is what "preserve the value verbatim" actually requires.

    THE LAST-NUMBER HEURISTIC IS A LATENT FALSE NEGATIVE. `correct` keyed off
    the final number in the response, on the reasoning that a worked solution
    ends with its answer. But a model that answers correctly and then cites
    its inputs - "7672.6 m/s, using Earth's radius (6371000 m)" - ends on
    6371000 and was scored WRONG. That is the documented instrument-bug
    pattern: what would this check score for an agent that did the work
    perfectly but behaved slightly unusually?

    Scanned across every text-rung sample on record (E01 3B, E03/E04/E05 3B,
    E01 8B): the shape occurs - E01 R1 s1 records 6371000.0, the trailing
    radius citation, as its "answer" - but produced ZERO false negatives,
    because in every such case the model's own answer was wrong too. So this
    fix changes no result that has ever been published. It is recorded that
    way deliberately: a scoring change that raises pass rates has to be shown
    not to be the reason a number moved.

    `correct_last_number` keeps the old verdict alongside the new one so the
    two readings can never silently diverge.
    """
    ns = numbers(text)
    hits = [n for n in ns if expected and abs(n - expected) / abs(expected) <= tol]
    last = ns[-1] if ns else None
    correct_last = bool(last is not None and expected
                        and abs(last - expected) / abs(expected) <= tol)

    # Prefer a number the model LABELLED with the output key - that is its
    # stated answer - and fall back to the trailing number only when it did
    # not label one.
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
    """Read the grader's expected value. `grading_dir` is explicit, not implied.

    It used to resolve `Path("grading")` against the process CWD, which is the
    same shape as SPEC-DELTA C9 - a path resolved against wherever the harness
    happened to be launched from.
    """
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
    """One rung, `samples` times. R4/R5 use the real loop and real grader.

    `step_by_step` adds STEP_BY_STEP to whatever the rung already sends. At
    R0-R3 that is the contract; at R4/R5 it is the suite's own goal text,
    which is where the real v3 change would land (the goal template in
    bench/suites/aero_calc.py). Applying it to the goal rather than to the
    system prefix is deliberate: it keeps this a probe of the ACTUAL proposed
    change, not of a different intervention that happens to work.
    """
    out_key, expected, tol = expected_for(key, grading_dir)
    goal = goal_text(ctx, key)
    out = []

    for i in range(samples):
        t0 = time.time()
        if rung not in LOOP_RUNGS:
            # EVERY rung must be ANSWERABLE from its own prompt. The first
            # version of R1 was the suite goal verbatim, which says "read
            # work/E01/input/params.json" and "use run_code" - at R1 there is
            # no file access and no tool, so the model could not know mu,
            # radius or altitude and the rung was unanswerable. A failure
            # there would have measured my prompt, not the scaffold.
            #
            # So the physics VALUES stay inline at every text rung, and what
            # escalates is the scaffolding around them.
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
                   # 1E: the whole response, not a 1500-char prefix. v2 R5
                   # sample 1 is permanently unclassifiable because its value
                   # was never written down; truncation is how that happens.
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
                # 1E: keep the bytes. A parse failure used to leave `got=None`
                # and no trace of WHAT failed to parse.
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


def main() -> int:
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

    # Declared unisolated, not defaulted. The ladder measures SCAFFOLDING
    # rungs on single tasks - there are no arms to cross-contaminate - and it
    # carries no retrieval tool. Recorded so a reader can tell that from
    # nobody having thought about it. D18.
    ctx = open_measurement(args.config, profile="local", isolate=False,
                           why="scaffolding rungs on single tasks: no arms, "
                               "no retrieval")
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.backends["cloud"].model = args.model
    from agent.models.registry import build_backends

    ctx.backends = build_backends(ctx.config)

    data = load_bare_prompts(args.bare_prompts)
    bare_for(data, args.task)      # fail fast, before any model call
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


if __name__ == "__main__":
    raise SystemExit(main())
