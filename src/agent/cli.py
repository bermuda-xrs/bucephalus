"""The terminal client. A client of M12's API, not a second implementation.

Rendering rule from M13.6 that matters more than it looks: integrity alerts do
NOT share a visual treatment with approvals. `danger` is reserved for tamper
alerts and used for nothing else, so a broken hash chain can never be approved
by muscle memory.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .assure.monitor import flags as monitor_flags
from .context import AgentContext
from .execute.loop import run_task
from .instrument.report import pareto_points, run_report, write_calls_csv, write_run_json
from .kernel.integrity import verify_all
from .kernel.tasks import create_task, load_task, set_status
from .kernel.types import Budget, TaskContract
from .plan.planner import approve_plan, draft_plan, load_plan, reject_plan
from .skills.gate import decay_pass, evaluate_skill, promote

DEFAULT_THEME = {
    "accent": "#7DB4C8",
    "success": "#88C070",
    "warning": "#D9A441",
    "danger": "#FF4B4B",  # integrity alerts ONLY — reserved
    "muted": "#7A7A7A",
}


def load_theme(ctx) -> dict:
    p = Path(ctx.config.resolve(ctx.config.ui.theme))
    if not p.exists():
        return DEFAULT_THEME
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        return {**DEFAULT_THEME, **data.get("colors", {})}
    except Exception:  # noqa: BLE001 - a broken theme must not stop the agent
        return DEFAULT_THEME


def banner(ctx, console: Console) -> None:
    p = Path(ctx.config.resolve(ctx.config.ui.banner_art))
    if p.exists():
        console.print(p.read_text(encoding="utf-8", errors="replace"))
    else:
        console.print("[bold]alpha test 1[/bold]")


# -- commands ---------------------------------------------------------------


def cmd_new(ctx, args, console) -> int:
    contract = TaskContract(
        goal=args.goal,
        success_criterion=args.criterion,
        artifact_scope=args.scope or [],
    )
    budget = Budget(
        usd=args.usd or ctx.config.limits.default_task_budget_usd,
        seconds=args.seconds or ctx.config.limits.default_task_budget_seconds,
    )
    task = create_task(ctx.conn, ctx.log, contract, budget)
    console.print(f"task [bold]{task.id}[/bold] created")
    console.print(f"next: alpha plan {task.id}")
    return 0


def cmd_plan(ctx, args, console) -> int:
    task = load_task(ctx.conn, args.task_id)
    theme = load_theme(ctx)
    plan = draft_plan(ctx, task)
    console.print(
        Panel(
            plan.render(),
            title="plan — human touch #1",
            border_style=theme["accent"],
        )
    )
    if plan.open_questions:
        console.print(
            f"[{theme['warning']}]answer the open questions before approving; "
            f"that is what keeps this to two touches.[/]"
        )
    console.print(f"approve: alpha approve {task.id} {plan.id}")
    return 0


def cmd_approve(ctx, args, console) -> int:
    task = load_task(ctx.conn, args.task_id)
    plan = load_plan(ctx, task.id)
    if plan is None:
        console.print("[red]no plan drafted for this task[/red]")
        return 1
    if args.plan_id and args.plan_id != plan.id:
        console.print(
            f"[red]plan hash mismatch: you approved {args.plan_id[:12]}, "
            f"current draft is {plan.id[:12]}[/red]"
        )
        return 1
    approve_plan(ctx, task, plan)
    console.print(f"plan {plan.id[:12]} approved and pinned")
    return 0


def cmd_reject(ctx, args, console) -> int:
    task = load_task(ctx.conn, args.task_id)
    plan = load_plan(ctx, task.id)
    if plan is None:
        console.print("[red]no plan to reject[/red]")
        return 1
    reject_plan(ctx, task, plan, args.comment or "")
    console.print("plan rejected; redraft with alpha plan")
    return 0


def cmd_run(ctx, args, console) -> int:
    theme = load_theme(ctx)
    task = load_task(ctx.conn, args.task_id)
    if ctx.config.autonomy.plan_required and ctx.approved_plan(task) is None:
        console.print("[red]plan_required is on: approve a plan first[/red]")
        return 1

    with console.status("working...", spinner="dots"):
        result = run_task(ctx, args.task_id)

    if result.status == "paused":
        console.print(
            Panel(
                f"{result.reason}\napproval id: {result.approval_id}\n"
                f"decide with: alpha approvals --decide {result.approval_id} --yes",
                title="parked for you",
                border_style=theme["warning"],
            )
        )
        return 0

    style = theme["success"] if result.status == "done" else theme["warning"]
    answer = result.answer if isinstance(result.answer, str) else json.dumps(result.answer, indent=2)
    console.print(
        Panel(
            f"{answer}",
            title=f"result — human touch #2 ({result.status}, {result.steps} steps)",
            border_style=style,
        )
    )
    _print_budget(ctx, console, args.task_id, theme)
    return 0 if result.status == "done" else 2


def _print_budget(ctx, console, task_id, theme) -> None:
    m = ctx.telemetry.metrics(task_id)
    console.print(
        f"[{theme['muted']}]paid {m.paid_tokens:,} tok · free {m.free_tokens:,} tok · "
        f"${m.cost_usd:.4f} · cache hit {m.cache_hit_ratio:.0%} · "
        f"{m.wall_ms/1000:.1f}s[/]"
    )
    alarm = ctx.telemetry.cache_alarm(task_id)
    if alarm:
        console.print(f"[{theme['warning']}]cache alarm: {alarm}[/]")


def cmd_status(ctx, args, console) -> int:
    theme = load_theme(ctx)
    t = Table(title="tasks")
    for c in ("task", "status", "goal", "cost", "steps"):
        t.add_column(c)
    rows = ctx.conn.execute(
        "SELECT id, status FROM tasks ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    for tid, status in rows:
        task = load_task(ctx.conn, tid)
        m = ctx.telemetry.metrics(tid)
        t.add_row(tid[:14], status, task.contract.goal[:48], f"${m.cost_usd:.4f}", str(m.n_calls))
    console.print(t)
    pend = ctx.pending_approvals()
    if pend:
        console.print(f"[{theme['warning']}]{len(pend)} approval(s) waiting — alpha approvals[/]")
    return 0


def cmd_approvals(ctx, args, console) -> int:
    theme = load_theme(ctx)
    if args.decide:
        out = ctx.resolve_approval(args.decide, approve=args.yes)
        console.print(f"{args.decide} → {'approved' if out['approved'] else 'denied'}")
        return 0
    pend = ctx.pending_approvals()
    if not pend:
        console.print("nothing waiting")
        return 0
    t = Table(title="pending approvals", border_style=theme["warning"])
    for c in ("id", "task", "tool", "preview", "why"):
        t.add_column(c, overflow="fold")
    for a in pend:
        t.add_row(a["id"], a["task_id"][:12], a["tool"], a["preview"][:60], a["reason"][:40])
    console.print(t)
    return 0


def cmd_alerts(ctx, args, console) -> int:
    """Integrity alerts render differently from approvals. On purpose."""
    theme = load_theme(ctx)
    findings = verify_all(ctx.conn, ctx.log, ctx.anchor)
    if findings:
        body = "\n".join(f"{f['task_id'][:12]}: {f['detail']}" for f in findings)
        console.print(
            Panel(
                Text(body, style="bold"),
                title="!! INTEGRITY — CHAIN OR ANCHOR MISMATCH !!",
                border_style=theme["danger"],
            )
        )
    else:
        console.print("integrity: all chains verify against the anchor")

    flags = monitor_flags(ctx.conn)
    if flags:
        t = Table(title="monitor findings (not shown to the agent)")
        for c in ("task", "severity", "finding"):
            t.add_column(c, overflow="fold")
        for f in flags[:20]:
            t.add_row(f["task_id"][:12], f["severity"], f["finding"][:80])
        console.print(t)
    return 1 if findings else 0


def cmd_verify(ctx, args, console) -> int:
    return cmd_alerts(ctx, args, console)


def cmd_report(ctx, args, console) -> int:
    report = run_report(ctx.conn, ctx.telemetry, args.task_id)
    out_dir = Path(ctx.config.root) / "runs"
    write_run_json(out_dir / f"{args.task_id}.json", report)
    write_calls_csv(ctx.conn, out_dir / f"{args.task_id}_calls.csv", args.task_id)
    console.print_json(json.dumps(report))
    console.print(f"written to {out_dir}")
    return 0


def cmd_pareto(ctx, args, console) -> int:
    points = pareto_points(ctx.conn, ctx.telemetry)
    t = Table(title="cost vs pass rate (the headline artifact, not a single score)")
    for c in ("arm", "n", "mean $/task", "pass rate"):
        t.add_column(c)
    for p in points:
        t.add_row(p["arm"], str(p["n"]), f"{p['mean_usd_per_task']:.4f}", f"{p['pass_rate']:.2%}")
    console.print(t)
    console.print(
        "[dim]report three ways — bare model, model+scaffold, full system — "
        "with date and versions, and state contamination caveats in the same "
        "paragraph as the number.[/dim]"
    )
    return 0


def cmd_skills(ctx, args, console) -> int:
    from .skills.generate import promotion_rate

    if args.add:
        # M9 needs a hand-written skill before it can calibrate. Everything
        # added here starts as a CANDIDATE: the gate is the only way to
        # promote, and it needs 30 paired trials first.
        path = Path(args.add)
        if not path.exists():
            console.print(f"[red]no such file: {path}[/red]")
            return 1
        body = path.read_text(encoding="utf-8")
        sid = ctx.skills.store(
            name=args.name or path.stem,
            body=body,
            contract={"applies_when": args.when or "(not stated)"},
            provenance={"drafted_by": "human", "source_file": str(path)},
            status="candidate",
        )
        console.print(f"stored [bold]{sid[:12]}[/bold] as a candidate")
        console.print(
            f"next: run paired trials, then\n"
            f"  python bench/run.py paired {sid} --suite bench/tasks"
        )
        return 0

    if args.gate:
        decision = evaluate_skill(ctx, args.gate)
        console.print_json(json.dumps({"verdict": decision.verdict, "reason": decision.reason,
                                       "detail": decision.detail}))
        if decision.verdict == "promote" and args.yes:
            promote(ctx, args.gate, decision, human_approved=True)
            console.print("[green]promoted[/green]")
        elif decision.verdict == "promote":
            console.print("gate passed. promotion needs a human: re-run with --yes")
        return 0
    if args.decay:
        for a in decay_pass(ctx):
            console.print(f"{a['skill']}: {a['action']} ({a['why']})")
        return 0

    t = Table(title="skills")
    for c in ("name", "v", "status", "id"):
        t.add_column(c)
    for row in ctx.conn.execute(
        "SELECT name, version, status, id FROM skills ORDER BY name, version"
    ).fetchall():
        t.add_row(row[0], str(row[1]), row[2], row[3][:12])
    console.print(t)
    console.print_json(json.dumps(promotion_rate(ctx.conn)))
    return 0


def cmd_bench_local(ctx, args, console) -> int:
    """Measure the local tiers. Spec 0.1c: settle model choice empirically.

    Reports observed tokens/sec and wall-clock per purpose so `active_local_tier`
    is a measurement rather than a guess.
    """
    theme = load_theme(ctx)
    t = Table(title="local tier — measured, not configured")
    for c in ("backend", "model", "reachable", "tok/s", "first call (s)", "out tokens"):
        t.add_column(c)

    prompt = (
        "Summarize in one sentence: the quick brown fox jumps over the lazy dog "
        "while the build system emits three warnings and one error."
    )
    for name in ("local", "laptop"):
        b = ctx.backends.get(name)
        if b is None:
            continue
        reachable = getattr(b, "available", lambda: True)()
        if not reachable:
            t.add_row(name, b.cfg.model, "[red]no[/red]", "-", "-", "-")
            continue
        t0 = time.time()
        try:
            resp = b.complete("You summarize. Be brief.", prompt, None, 128, 0.0)
            elapsed = time.time() - t0
            tps = resp.usage.output_tokens / max(elapsed, 1e-3)
            t.add_row(
                name, b.cfg.model, "[green]yes[/green]",
                f"{tps:.1f}", f"{elapsed:.1f}", str(resp.usage.output_tokens),
            )
        except Exception as e:  # noqa: BLE001
            t.add_row(name, b.cfg.model, f"[red]{type(e).__name__}[/red]", "-", "-", "-")
    console.print(t)
    console.print(
        f"[{theme['muted']}]On CPU-only hardware output tokens are wall-clock "
        f"seconds. Pick the tier whose tok/s keeps a compress step under a "
        f"second or two, and leave thinking off.[/]"
    )
    return 0


def cmd_steer(ctx, args, console) -> int:
    """M12.3 — never mutate a running task. Fork it, keeping history replayable."""
    from .kernel.types import TaskStatus

    parent = load_task(ctx.conn, args.task_id)
    ctx.log.append(args.task_id, "human.steered", {"correction": args.correction})
    child_contract = parent.contract.model_copy(
        update={"goal": parent.contract.goal + f"\n\n[steered] {args.correction}"}
    )
    child = create_task(
        ctx.conn, ctx.log, child_contract, parent.budget.model_copy(),
        parent_id=args.task_id,
    )
    set_status(ctx.conn, ctx.log, parent, TaskStatus.PAUSED, "superseded by steer")
    console.print(f"forked into [bold]{child.id}[/bold]; parent paused, history intact")
    console.print(f"next: alpha run {child.id}")
    return 0


def cmd_serve(ctx, args, console) -> int:
    import uvicorn

    from .control.server import create_app

    console.print(f"control plane on http://{args.host}:{args.port}")
    uvicorn.run(create_app(ctx), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_monitor(ctx, args, console) -> int:
    from .assure.monitor import run_monitor

    n = run_monitor(ctx, args.task_id)
    console.print(f"{n} finding(s) written to monitor_flags (agent never sees them)")
    return 0


# -- entrypoint -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alpha", description="alpha test 1")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="create a task")
    n.add_argument("goal")
    n.add_argument("--criterion", default="always_pass")
    n.add_argument("--scope", nargs="*")
    n.add_argument("--usd", type=float)
    n.add_argument("--seconds", type=int)
    n.set_defaults(fn=cmd_new)

    pl = sub.add_parser("plan", help="draft a plan (read-only)")
    pl.add_argument("task_id")
    pl.set_defaults(fn=cmd_plan)

    ap = sub.add_parser("approve", help="approve and pin a plan")
    ap.add_argument("task_id")
    ap.add_argument("plan_id", nargs="?")
    ap.set_defaults(fn=cmd_approve)

    rj = sub.add_parser("reject", help="reject a plan with a comment")
    rj.add_argument("task_id")
    rj.add_argument("--comment")
    rj.set_defaults(fn=cmd_reject)

    r = sub.add_parser("run", help="execute an approved plan")
    r.add_argument("task_id")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("status", help="list tasks")
    s.set_defaults(fn=cmd_status)

    a = sub.add_parser("approvals", help="list or decide pending approvals")
    a.add_argument("--decide")
    a.add_argument("--yes", action="store_true")
    a.set_defaults(fn=cmd_approvals)

    al = sub.add_parser("alerts", help="integrity + monitor findings")
    al.set_defaults(fn=cmd_alerts)

    v = sub.add_parser("verify", help="verify every chain against the anchor")
    v.set_defaults(fn=cmd_verify)

    rp = sub.add_parser("report", help="write the per-run JSON and CSV")
    rp.add_argument("task_id")
    rp.set_defaults(fn=cmd_report)

    pa = sub.add_parser("pareto", help="cost vs pass rate across arms")
    pa.set_defaults(fn=cmd_pareto)

    sk = sub.add_parser("skills", help="list skills, add one, run the gate or decay")
    sk.add_argument("--add", metavar="SKILL.md", help="store a hand-written skill as a candidate")
    sk.add_argument("--name", help="skill name (defaults to the filename)")
    sk.add_argument("--when", help="one line: when this skill applies")
    sk.add_argument("--gate", help="skill id to evaluate")
    sk.add_argument("--decay", action="store_true")
    sk.add_argument("--yes", action="store_true", help="human approval for promotion")
    sk.set_defaults(fn=cmd_skills)

    st = sub.add_parser("steer", help="correct a running task (forks; never mutates)")
    st.add_argument("task_id")
    st.add_argument("correction")
    st.set_defaults(fn=cmd_steer)

    bl = sub.add_parser("bench-local", help="measure local tier throughput")
    bl.set_defaults(fn=cmd_bench_local)

    mo = sub.add_parser("monitor", help="run the out-of-band monitor over a task")
    mo.add_argument("task_id")
    mo.set_defaults(fn=cmd_monitor)

    sv = sub.add_parser("serve", help="run the control plane")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8901)
    sv.set_defaults(fn=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    ctx = AgentContext.open(args.config)
    try:
        if args.cmd in ("status", "new"):
            banner(ctx, console)
        return args.fn(ctx, args, console)
    except Exception as e:  # noqa: BLE001 - the CLI reports, it does not traceback at users
        console.print(f"[bold red]{type(e).__name__}[/bold red]: {e}")
        return 1
    finally:
        ctx.close()


if __name__ == "__main__":
    sys.exit(main())
