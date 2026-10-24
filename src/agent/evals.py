from __future__ import annotations

import copy
import itertools
import json
import time
from math import comb
from pathlib import Path

SECTIONS = ("mechanisms", "limits", "autonomy")
TUNABLE: dict[str, dict[str, list]] = {
    "long_files": {"long_files": ["pages", "elide", "whole"]},
    "read_all_pages": {"backend.read_all_pages": [None, False]},
    "fill_ratio": {"backend.context_fill_ratio": [0.28, 0.5]},
}


def apply_overrides(raw: dict, overrides: dict, slot: str = "cloud") -> dict:
    raw = copy.deepcopy(raw)
    for key, value in overrides.items():
        head, _, rest = key.partition(".")
        if head == "backend" and rest:
            raw["backends"][slot][rest] = value
        elif head in SECTIONS and rest:
            raw.setdefault(head, {})[rest] = value
        elif not rest and head in ("long_files", "compress_observations_over_tokens"):
            raw[head] = value
        else:
            raise ValueError(f"unknown override {key!r}")
    return raw


def nested(overrides: dict, slot: str = "cloud") -> dict:
    return apply_overrides({"backends": {slot: {}}}, overrides, slot)


def measurement_raw(raw: dict, model: str | None = None) -> dict:
    raw = copy.deepcopy(raw)
    raw.setdefault("autonomy", {}).update({"auto_review": False, "approve_scoped_writes": True})
    raw.setdefault("limits", {})["max_parallel_tools"] = 1
    raw.setdefault("channels", {}).setdefault("telegram", {})["enabled"] = False
    raw.update(grader_held_out=True, isolate_retrieval=True)
    if model:
        raw["backends"]["cloud"]["model"] = model
    return raw


def provision(root: Path, spec: dict) -> None:
    for rel, text in spec.get("fixtures", {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    for scope in spec.get("artifact_scope", []):
        (root / scope).mkdir(parents=True, exist_ok=True)
    for name, text in spec.get("graders", {}).items():
        (root / spec["grader_dir"]).mkdir(parents=True, exist_ok=True)
        (root / spec["grader_dir"] / name).write_text(text, encoding="utf-8")


def _context(raw: dict, root: Path, backends_factory=None, profile: str | None = "local"):
    from .config import config_from_raw
    from .context import AgentContext
    from .paths import ensure_state_dirs

    r = copy.deepcopy(raw)
    r.update(root=str(root), state_dir="", db_path="./agent.db", anchor_path="./anchor/chain.anchor",
             skills_path="./skills_store", source_path="")
    ensure_state_dirs(root)
    cfg = config_from_raw(r, profile)
    return AgentContext(cfg, backends=backends_factory(cfg) if backends_factory else None)


COUNTED = ("read_first.held", "pages.unread_held", "verify.continued", "stuck.warned", "stuck.detected",
           "session.opened", "approval.requested", "tool.receipt")


def run_instance(raw: dict, spec: dict, root: Path, backends_factory=None, profile: str | None = "local") -> dict:
    from .execute.loop import run_task
    from .kernel.tasks import create_task
    from .kernel.types import Budget, TaskContract

    root.mkdir(parents=True, exist_ok=True)
    provision(root, spec)
    ctx = _context(raw, root, backends_factory, profile)
    out = {"key": spec["key"], "family": spec.get("family"), "seed": spec.get("seed"), "split": spec.get("split")}
    try:
        contract = TaskContract(goal=spec["goal"], success_criterion=spec["success_criterion"],
                                output_schema=spec.get("output_schema", {}), artifact_scope=spec.get("artifact_scope", []),
                                grader_dir=spec.get("grader_dir"))
        task = create_task(ctx.conn, ctx.log, contract, Budget(usd=spec.get("budget_usd", 0.0),
                                                               seconds=spec.get("budget_seconds", 600)))
        ctx.set_frozen_skills(task.id, [])
        t0 = time.time()
        try:
            status = run_task(ctx, task.id).status
        except Exception as e:  # noqa: BLE001
            status, out["error"] = "crashed", f"{type(e).__name__}: {e}"[:300]
        rows = ctx.conn.execute("SELECT type, COUNT(*) FROM events WHERE task_id=? GROUP BY type", (task.id,)).fetchall()
        tokens = ctx.conn.execute("SELECT COALESCE(SUM(est_input_tokens), 0), COUNT(*) FROM calls WHERE task_id=?",
                                  (task.id,)).fetchone()
        counts = dict(rows)
        out.update(status=status, passed=status == "done", wall_s=round(time.time() - t0, 2),
                   events={k: counts.get(k, 0) for k in COUNTED}, prompt_tokens=tokens[0], model_calls=tokens[1])
    finally:
        ctx.close()
    return out


def card_for(raw: dict, root: Path, backends_factory=None, profile: str | None = None) -> dict:
    from .card import harness_card

    root.mkdir(parents=True, exist_ok=True)
    ctx = _context(raw, root, backends_factory, profile)
    try:
        return harness_card(ctx)
    finally:
        ctx.close()


def run_arm(raw: dict, specs: list[dict], out: Path, backends_factory=None, profile: str | None = "local") -> list[dict]:
    return [run_instance(raw, spec, out / spec["key"], backends_factory, profile) for spec in specs]


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def summarise(by_arm: dict[str, list[dict]], base: str) -> dict:
    index = {a: {r["key"]: r for r in rows} for a, rows in by_arm.items()}
    families: dict = {}
    for a, rows in index.items():
        for r in rows.values():
            families.setdefault(r.get("family"), {}).setdefault(a, 0)
            families[r.get("family")][a] += bool(r["passed"])
    vs = {}
    for a in index:
        if a == base:
            continue
        keys = index[a].keys() & index.get(base, {}).keys()
        B, A = index[base], index[a]
        b = sum(B[k]["passed"] and not A[k]["passed"] for k in keys)
        c = sum(A[k]["passed"] and not B[k]["passed"] for k in keys)
        vs[a] = {"n": len(keys), "base_passes": sum(B[k]["passed"] for k in keys),
                 "arm_passes": sum(A[k]["passed"] for k in keys), "only_base": b, "only_arm": c,
                 "both": sum(B[k]["passed"] and A[k]["passed"] for k in keys),
                 "neither": sum(not B[k]["passed"] and not A[k]["passed"] for k in keys),
                 "mcnemar_exact_p": round(mcnemar_exact(b, c), 4)}
    return {"n": len(index.get(base, {})), "n_families": len(families),
            "arms": {a: {"passes": sum(bool(r["passed"]) for r in rows.values()), "n": len(rows)} for a, rows in index.items()},
            "families": families, "vs_base": vs}


def design(flags: dict[str, dict[str, list]], max_configs: int = 16) -> tuple[list[dict], str]:
    axes = [[(k, v) for v in values] for spec in flags.values() for k, values in spec.items()]
    total = 1
    for axis in axes:
        total *= len(axis)
    if total <= max_configs:
        configs = []
        for combo in itertools.product(*axes):
            configs.append({k: v for (k, v), axis in zip(combo, axes) if v != axis[0][1]})
        return configs, "factorial"
    configs = [{}]
    for axis in axes:
        configs += [{k: v} for k, v in axis[1:]]
    return configs, "one_at_a_time"


def tune(raw: dict, flags: dict, dev: list[dict], heldout: list[dict], out: Path, backends_factory=None,
         max_configs: int = 16, profile: str | None = "local") -> dict:
    configs, how = design(flags, max_configs)
    scored = []
    for i, overrides in enumerate(configs):
        rows = run_arm(apply_overrides(raw, overrides), dev, out / "dev" / f"c{i}", backends_factory, profile)
        scored.append({"overrides": overrides, "passes": sum(r["passed"] for r in rows), "n": len(rows)})
    best = max(scored, key=lambda s: (s["passes"], -len(s["overrides"])))
    winner = best["overrides"]
    base = run_arm(raw, heldout, out / "heldout" / "base", backends_factory, profile)
    held = {"base": base, "winner": run_arm(apply_overrides(raw, winner), heldout, out / "heldout" / "winner",
                                            backends_factory, profile) if winner else base}
    result = {"design": how, "dev": scored, "winner": winner, "heldout": summarise(held, "base"),
              "card": card_for(apply_overrides(raw, winner), out / "card", backends_factory), "profile": nested(winner)}
    (out / "tune.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result
