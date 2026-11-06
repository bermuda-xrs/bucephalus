"""Pool the outputs of `families_run.py --shard K/N` runs (one folder per machine) into one result."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def merge(dirs: list[Path], out: Path) -> dict:
    from agent import evals

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
