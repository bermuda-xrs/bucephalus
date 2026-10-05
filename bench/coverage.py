from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

TOPICS = {
    "units": re.compile(
        r"km/h|km |minutes|hours|seconds to|to seconds|to days|units|SI", re.I),
    "body/radius": re.compile(
        r"altitude|radius|radii|body|constants|Mars|Moon|Earth|mu\b", re.I),
    "isp/trig/magnitude": re.compile(
        r"Isp|g0|radians|degrees|magnitude|half angle", re.I),
}


def skill_topics(text: str) -> set[str]:
    return {name for name, rx in TOPICS.items() if rx.search(text)}


def load_tasks(tree: Path) -> list[dict]:
    import yaml

    out = []
    for split in ("drafting", "eval"):
        d = tree / split
        if not d.is_dir():
            continue
        for task in sorted(d.iterdir()):
            meta_file = task / "meta.yaml"
            if not meta_file.exists():
                continue
            meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
            out.append({"key": task.name, "tier": meta.get("tier", "?"),
                        "split": meta.get("split", split),
                        "trap": meta.get("trap") or "none"})
    return out


def coverage(tasks: list[dict], topics: set[str]) -> list[dict]:
    rows = []
    for t in tasks:
        hit = sorted(name for name in topics
                     if TOPICS[name].search(t["trap"]))
        rows.append({**t, "topics": hit, "covered": bool(hit)})
    return rows


def report(rows: list[dict], min_discordant: int, split: str | None = None) -> str:
    if split:
        rows = [r for r in rows if r["split"] == split]
    L = [f"{'task':<6} {'tier':<7} {'split':<9} {'covered':<8} trap",
         "-" * 92]
    for r in rows:
        L.append(f"{r['key']:<6} {r['tier']:<7} {r['split']:<9} "
                 f"{','.join(r['topics']) if r['topics'] else 'NO':<8} "
                 f"{r['trap'][:44]}")

    by_tier: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        by_tier[r["tier"]][1] += 1
        by_tier[r["tier"]][0] += int(r["covered"])
    L.append("")
    L.append("tasks whose documented trap this skill addresses:")
    for tier in ("easy", "medium", "hard"):
        if tier in by_tier:
            c, n = by_tier[tier]
            L.append(f"  {tier:<7} {c}/{n}")
    cov = sum(1 for r in rows if r["covered"])
    L.append(f"  {'ALL':<7} {cov}/{len(rows)}")
    L.append("")
    L.append(f"UPPER BOUND on discordant pairs: {cov}. "
             f"Gate floor: {min_discordant}.")
    if cov < min_discordant:
        L.append("")
        L.append(
            f"STOP. At most {cov} tasks can disagree between arms, which "
            f"cannot clear a floor of {min_discordant}. The calibration would "
            "report 'insufficient' after spending the full run, and that "
            "verdict would say nothing about the skill. Widen the skill, or "
            "add tasks whose traps it addresses.")
    else:
        L.append("")
        L.append(
            "Enough tasks are in scope for the floor to be reachable. This is "
            "an upper bound, NOT a prediction: covering a trap is not fixing "
            "it, and every covered task still has to actually change outcome.")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="upper bound on discordant pairs")
    ap.add_argument("--skill", required=True, help="path to the skill markdown")
    ap.add_argument("--tasks", required=True,
                    help="path to the emitted task tree. REQUIRED - there is "
                         "no default. The aerospace tree was the default for "
                         "this project's whole history, which is how it "
                         "became load-bearing without anyone choosing it")
    ap.add_argument("--split", choices=["drafting", "eval"])
    ap.add_argument("--min-discordant", type=int, default=10)
    args = ap.parse_args()

    text = Path(args.skill).read_text(encoding="utf-8")
    topics = skill_topics(text)
    print(f"skill: {args.skill}")
    print(f"topics claimed: {', '.join(sorted(topics)) or 'none'}\n")
    rows = coverage(load_tasks(Path(args.tasks)), topics)
    print(report(rows, args.min_discordant, args.split))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
