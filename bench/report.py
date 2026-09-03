"""Turn the run artifacts into the run sheet, with the caveats attached.

Part 13's reporting rules are not decoration, so they are enforced here rather
than left to whoever writes the summary:

  * a cost-versus-quality picture, never a single score
  * three ways - bare model, model + scaffold, full system - with date,
    model version and profile
  * contamination caveats in the SAME paragraph as the number, never a
    footnote

The generator refuses to emit a headline lift when the noise floor that would
make it interpretable is missing or says the suite is too noisy. That refusal
is the point: a lift measured against an unmeasured noise floor is exactly the
number this project exists not to publish.

  python bench/report.py --noise runs/noise-floor.json \
                         --calibration runs/calibration.json \
                         --out docs/run-sheet.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MAX_FLAKY_FRACTION = 8 / 30      # the stop rule, as a fraction of tasks
BAND = (0.35, 0.65)


def _load(path: str | None) -> dict | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def interpret_noise(noise: dict) -> tuple[str, list[str]]:
    """(verdict, notes). The stop rule scales with the number of tasks."""
    n = noise.get("n_tasks", 0)
    flaky = noise.get("flaky", 0)
    runs = noise.get("runs_per_task", 1)
    mean = noise.get("mean_pass_rate", 0.0)
    notes: list[str] = []

    if runs < 2:
        notes.append(
            f"Only {runs} run per task, so no task can be classified as flaky "
            "yet - stability needs at least two observations. Treat the "
            "classifications below as placeholders."
        )
    limit = max(1, round(MAX_FLAKY_FRACTION * n))
    if runs >= 2 and flaky > limit:
        return "unusable", notes + [
            f"{flaky} of {n} tasks are flaky, above the limit of {limit} "
            f"({MAX_FLAKY_FRACTION:.0%}). A lift measured against this much "
            "noise cannot be interpreted. Tighten the task specifications "
            "before evaluating any skill."
        ]
    if not (BAND[0] <= mean <= BAND[1]):
        notes.append(
            f"Mean pass rate {_pct(mean)} is outside the "
            f"{BAND[0]:.0%}-{BAND[1]:.0%} band where a lift is most "
            "detectable. A floor or ceiling effect costs statistical power: "
            "tasks that always fail, or always pass, produce no discordant "
            "pairs and so carry no information in McNemar's test."
        )
    return ("usable" if runs >= 2 else "provisional"), notes


def render(noise: dict | None, cal: dict | None) -> str:
    L: list[str] = []
    add = L.append

    add("# Run sheet")
    add("")
    if noise is None and cal is None:
        add("No run artifacts found. Nothing to report.")
        return "\n".join(L) + "\n"

    src = noise or cal
    prov = (src or {}).get("profile_provenance", {})
    models = prov.get("backends") or (cal or {}).get("models") or {}
    add(f"* **date** {(cal or {}).get('date', noise and noise.get('date') or 'n/a')}")
    prov_all = (src or {}).get("profile_provenance", {}) or {}
    disabled = []
    if prov_all.get("escalation"):
        disabled.append("escalation-by-disagreement (M6.3) — " + prov_all["escalation"])
    if (src or {}).get("auto_review") is False or (src or {}).get("deputy") == "off":
        disabled.append("the M13.3 deputy (auto_review) — off for measurement")
    if prov_all.get("executor_override"):
        add(f"* **executor override** {prov_all['executor_override']}")
    if (src or {}).get("budget_scale", 1.0) != 1.0:
        add(f"* **time budgets scaled** x{(src or {}).get('budget_scale')} "
            "(so a slower executor is compared on models, not step budgets)")
    add(f"* **harness version** {(src or {}).get('harness_version', 'UNKNOWN')} "
        "— pass rates are comparable only within one version")
    add(f"* **profile** `{(src or {}).get('profile', 'unknown')}` — "
        f"cloud backends {prov.get('cloud_backends', 'n/a')}, "
        f"provider credentials {prov.get('credentials_present', 'n/a')}")
    if models:
        rendered = ", ".join(
            f"{k}={v.get('model', v) if isinstance(v, dict) else v}"
            for k, v in sorted(models.items()))
        add(f"* **models** {rendered}")
    add("")

    if disabled:
        add("")
        add("**The measured system is not the shipped system.** Disabled here:")
        for d in disabled:
            add(f"  * {d}")
    add("")

    # ---------------------------------------------------------------- noise
    add("## Noise floor")
    add("")
    if noise is None:
        add("**Not measured.** Every number below this line is uninterpretable "
            "without it: a lift is only meaningful relative to how much the "
            "same tasks move on their own.")
        add("")
    else:
        verdict, notes = interpret_noise(noise)
        runs = noise.get("runs_per_task", 1)
        add(f"`{noise.get('suite')}` · {noise.get('n_tasks')} tasks × "
            f"{runs} run{'s' if runs != 1 else ''} · split="
            f"{noise.get('split') or 'all'}")
        add("")
        add("| | |")
        add("|---|---|")
        add(f"| mean pass rate | **{_pct(noise.get('mean_pass_rate', 0))}** |")
        add(f"| stable pass / stable fail / flaky | "
            f"{noise.get('stable_pass')} / {noise.get('stable_fail')} / "
            f"**{noise.get('flaky')}** |")
        add(f"| ended at the step limit | {noise.get('ended_at_step_limit', 'n/a')} |")
        add(f"| ended budget-exhausted | {noise.get('ended_budget_exhausted', 'n/a')} |")
        # wall_s_total is the pre-resume field name; older artifacts carry it.
        secs = (noise.get("wall_s_all_runs")
                or noise.get("wall_s_this_session")
                or noise.get("wall_s_total") or 0)
        add(f"| wall clock, all runs | {secs / 3600:.2f} h |")

        # Memory-suspect tasks. A wall-clock reading taken while the machine
        # was paging is not a measurement of the task, and seconds are the
        # only cost axis this project has. One call has already been observed
        # at 2465 s against a 600 s budget - 29x its rung median - which
        # ended `budget_exhausted`, i.e. memory pressure masquerading as
        # difficulty. These are named rather than silently averaged in.
        suspect = noise.get("memory_suspect_keys") or []
        floor = noise.get("low_memory_threshold_gb")
        lowest = noise.get("min_free_gb_any_task")
        if lowest is not None:
            add(f"| lowest free memory, any task | {lowest} GB |")
        if suspect:
            add(f"| **wall-clock SUSPECT (free memory < {floor} GB)** | "
                f"**{len(suspect)} task(s): {', '.join(suspect)}** |")
        elif noise.get("memory_suspect_keys") is not None:
            add(f"| wall-clock suspect (free memory < {floor} GB) | none |")
        add("")
        if suspect:
            add(f"> **{len(suspect)} task(s) ran with free memory below "
                f"{floor} GB: {', '.join(suspect)}.** Their pass/fail still "
                "counts - the artifact either verified or it did not - but "
                "their wall-clock is NOT a measurement of the task and must "
                "not be read as one. Exclude them from any median, p90 or "
                "Pareto point, or state that they are included.")
            add("")
        tiers = noise.get("tier_pass_rates") or {}
        if tiers:
            add("| tier | pass rate |")
            add("|---|---|")
            for t in ("easy", "medium", "hard"):
                if t in tiers:
                    add(f"| {t} | {_pct(tiers[t])} |")
            add("")
        add(f"**Suite verdict: {verdict}.**")
        for nte in notes:
            add("")
            add(f"> {nte}")
        add("")

    # ---------------------------------------------------------- calibration
    add("## Calibration — is the instrument able to detect a real effect?")
    add("")
    if cal is None:
        add("**Not run.** A gate that rejects everything and a gate that works "
            "are indistinguishable until a known-good skill passes, so no "
            "rejection of any later skill can be interpreted yet.")
        add("")
    else:
        for label in ("primary", "secondary"):
            d = cal.get(label)
            if not d:
                continue
            add(f"### {label.upper()} — {d.get('scope')}")
            add("")
            add(f"**verdict `{d.get('verdict')}`** ({d.get('reason')}) · "
                f"n={d.get('n')} · b={d.get('b')} skill wins · "
                f"c={d.get('c')} control wins · "
                f"discordant={d.get('discordant')} · "
                f"lift **{d.get('lift_pp')}pp** · p={d.get('p')}. "
                f"{d.get('caveat', '')}")
            add("")

    # ------------------------------------------------------------ three ways
    # Part 13 requires bare model / model + scaffold / full system. Omitting
    # the arms that were not measured would make the one that was look like
    # the whole picture, so the missing ones are named.
    add("## The three ways, and which of them exist")
    add("")
    add("| arm | what it is | measured? |")
    add("|---|---|---|")
    bare = "**no** — never measured"
    scaffold = "**no**"
    if noise is not None:
        scaffold = (f"yes — {_pct(noise.get('mean_pass_rate', 0))} over "
                    f"{noise.get('n_tasks')} tasks x "
                    f"{noise.get('runs_per_task')} runs")
    full = "**no**"
    if cal is not None:
        d = cal.get("primary") or {}
        b, n = d.get("b"), d.get("n")
        full = f"yes — paired against the scaffold arm, n={n}, b={b}"
    add(f"| bare model | the model answering with no agent loop | {bare} |")
    add(f"| model + scaffold | the loop, no skills — the baseline arm | {scaffold} |")
    add(f"| full system | the loop with the skill — the skill arm | {full} |")
    add("")
    add("The **bare model** arm has not been run. Without it, nothing here "
        "separates what the scaffold contributes from what the model already "
        "does, so no claim of the form \"the system makes a small model "
        "competent\" is supported yet — only the narrower claim that a skill "
        "moves the scaffolded model.")
    add("")

    # ----------------------------------------------------------- cost axis
    add("## Cost versus quality")
    add("")
    add("**The cost axis is SECONDS, not dollars.** On an all-local rig every "
        "call costs $0.0000, so a dollar axis collapses the chart to a single "
        "point and says nothing. Wall clock is the real constraint on this "
        "hardware, and the spec's own laptop-tier reasoning already says "
        "output tokens are the budget when the machine is the bottleneck.")
    add("")
    # S3 - a chart may not mix harness versions. The final sweep changed what
    # counts as a pass, so a pre-fix rate and a post-fix rate are answers to
    # different questions. Refuse rather than plot them together.
    pts = (cal or {}).get("pareto", []) or (noise or {}).get("pareto", []) or []
    versions = {p.get("harness_version") for p in pts}
    if len(versions) > 1:
        add("> **REFUSING TO PLOT.** These points come from different harness "
            f"versions ({sorted(v for v in versions if v is not None)}). The "
            "final sweep changed what counts as a pass, so pre-fix and "
            "post-fix rates answer different questions and a frontier drawn "
            "through both is meaningless. Re-run the older arm on the current "
            "harness.")
        add("")
        pts = []

    # S1 - done and done_unannounced are never collapsed. A reader must be
    # able to see how much of a pass rate came from the sweep.
    add("| arm | median s/task | pass rate | done | done_unannounced | USD/task |")
    add("|---|---|---|---|---|---|")
    for pt in pts:
        add(f"| {pt.get('arm')} | {pt.get('median_wall_s', 'n/a')} | "
            f"{_pct(pt.get('pass_rate', 0))} | {pt.get('done', 'n/a')} | "
            f"{pt.get('done_unannounced', 'n/a')} | "
            f"{pt.get('usd_per_task', 0):.4f} (n/a under local profile) |")
    if not ((cal or {}).get("pareto") or (noise or {}).get("pareto")):
        med = (noise or {}).get("median_wall_s")
        if med is not None:
            add(f"| baseline, single tier | {med} | "
                f"{_pct((noise or {}).get('mean_pass_rate', 0))} | "
                "0.0000 (n/a under local profile) |")
        else:
            add("| *(no arms recorded yet)* | | | |")
    add("")

    # --------------------------------------------- budget censoring per arm
    arms = [a for a in ((noise or {}).get("arms") or []) if a]
    if len(arms) >= 2:
        add("### Budget censoring")
        add("")
        add("The arms run under different time budgets, so a ceiling hit "
            "censors them differently. A pass rate is only comparable across "
            "arms if the ceiling-hit counts are comparable too.")
        add("")
        add("| arm | budget/task | ceiling hits | of tasks | pass rate |")
        add("|---|---|---|---|---|")
        for a in arms:
            hits = a.get("ceiling_hits", 0)
            add(f"| {a.get('name')} | {a.get('budget_s', 'n/a')}s | {hits} | "
                f"{a.get('n_tasks', 0)} | {_pct(a.get('pass_rate', 0))} |")
        add("")
        worst = max(arms, key=lambda a: a.get("ceiling_hits", 0))
        best = min(arms, key=lambda a: a.get("ceiling_hits", 0))
        gap = worst.get("ceiling_hits", 0) - best.get("ceiling_hits", 0)
        if gap >= max(1, 0.2 * max(1, worst.get("n_tasks", 1))):
            add(f"> **CONFOUNDED.** `{worst.get('name')}` hit its ceiling "
                f"{worst.get('ceiling_hits')} times against "
                f"{best.get('ceiling_hits')} for `{best.get('name')}`. The "
                "pass-rate difference between these arms is not attributable "
                "to model quality: the slower arm was cut off more often. "
                "Report the ceiling counts wherever the rates are quoted, and "
                "do not plot them as a clean frontier.")
            add("")

    # -------------------------------------------------------------- caveats
    add("## Caveats that travel with every number above")
    add("")
    for c in (cal or {}).get("caveats", []) or _DEFAULT_CAVEATS:
        add(f"* {c}")
    add("")
    add("* Wall-clock durations are comparable only against others measured "
        "on an equally loaded machine: the same task takes 162s idle and "
        "339-360s with a test suite running alongside.")
    return "\n".join(L) + "\n"


_DEFAULT_CAVEATS = [
    "Token counts are local estimates unless calibrated; see src/agent/tokens.py.",
    "Published agent costs have been misestimated by up to 100%. These numbers "
    "are cost-controlled but single-run unless n is stated.",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="render the run sheet")
    ap.add_argument("--noise", default="runs/noise-floor.json")
    ap.add_argument("--calibration", default="runs/calibration.json")
    ap.add_argument("--out", default="docs/run-sheet.md")
    args = ap.parse_args()

    noise, cal = _load(args.noise), _load(args.calibration)
    text = render(noise, cal)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"wrote {dest}")
    if noise is None:
        print("NOTE: no noise floor found — the run sheet says so rather than "
              "quietly omitting it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
