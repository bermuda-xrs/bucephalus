"""Pool the outputs of `families_run.py --shard K/N` runs (one folder per machine) into one result."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _check_plans(dirs: list[Path]) -> list[dict]:
    plans = []
    for d in dirs:
        f = Path(d) / "plan.json"
        if not f.exists():
            raise ValueError(f"{d} has no plan.json; run it with a families_run.py that writes one")
        plans.append(json.loads(f.read_text(encoding="utf-8")))
    fixed = [{k: v for k, v in p.items() if k not in ("shard", "keys")} for p in plans]
    for d, f in zip(dirs, fixed):
        diff = sorted(k for k in f.keys() | fixed[0].keys() if f.get(k) != fixed[0].get(k))
        if diff:
            raise ValueError(f"{d} ran with different settings from {dirs[0]}: {', '.join(diff)}")
    shards = [tuple(map(int, p["shard"].split("/"))) for p in plans]
    ns = {n for _, n in shards}
    if len(ns) != 1:
        raise ValueError(f"every shard must use the same N: {sorted(p['shard'] for p in plans)}")
    n = ns.pop()
    ks = sorted(k for k, _ in shards)
    if ks != list(range(n)):
        missing = [f"{k}/{n}" for k in range(n) if k not in ks]
        twice = sorted({f"{k}/{n}" for k in ks if ks.count(k) > 1})
        raise ValueError(f"shards {', '.join(missing)} are missing" if missing else f"shards {', '.join(twice)} given twice")
    return plans


def merge(dirs: list[Path], out: Path) -> dict:
    from agent import evals

    plans = _check_plans(dirs)
    by_arm: dict[str, dict] = {}
    for d in dirs:
        for f in sorted(Path(d).glob("*.jsonl")):
            rows = by_arm.setdefault(f.stem, {})
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    if r["key"] in rows:
                        raise ValueError(f"{f.stem}: {r['key']} was measured in more than one shard")
                    rows[r["key"]] = r
    for d, p in zip(dirs, plans):
        for arm, keys in p["keys"].items():
            left = [k for k in keys if k not in by_arm.get(arm, {})]
            if left:
                raise ValueError(f"{d} ({p['shard']}) has not finished {arm}: {len(left)} left, first {', '.join(left[:5])}")
    out.mkdir(parents=True, exist_ok=True)
    for arm, rows in by_arm.items():
        (out / f"{arm}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows.values()), encoding="utf-8")
    s = evals.summarise({a: list(r.values()) for a, r in by_arm.items()}, "base")
    s["shards"] = len(dirs)
    s["shard_cards"] = [json.loads((Path(d) / "cards.json").read_text(encoding="utf-8"))
                        for d in dirs if (Path(d) / "cards.json").exists()]
    (out / "summary.json").write_text(json.dumps(s, indent=2, default=str), encoding="utf-8")
    return s


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dirs", nargs="+", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)
    s = merge(a.dirs, a.out)
    print(json.dumps({k: s[k] for k in ("n", "n_families", "arms", "vs_base")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
