from __future__ import annotations

import argparse
import json
import os
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
    "danger": "#FF4B4B",
    "muted": "#7A7A7A",
}


def _asset(configured: str, ctx) -> Path | None:
    p = Path(ctx.config.resolve(configured))
    if p.exists():
        return p
    packaged = Path(__file__).parent / Path(configured).name
    for sub in ("themes", "assets"):
        cand = Path(__file__).parent / sub / Path(configured).name
        if cand.exists():
            return cand
    return packaged if packaged.exists() else None


def load_theme(ctx) -> dict:
    p = _asset(ctx.config.ui.theme, ctx)
    if p is None or not p.exists():
        return DEFAULT_THEME
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        return {**DEFAULT_THEME, **data.get("colors", {})}
    except Exception:  # noqa: BLE001
        return DEFAULT_THEME


def banner(ctx, console: Console) -> None:
    p = _asset(ctx.config.ui.banner_art, ctx)
    if p is not None and p.exists():
        console.print(p.read_text(encoding="utf-8", errors="replace"))
    else:
        console.print("[bold]alpha test 1[/bold]")


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
    if not contract.artifact_scope:
        console.print("[yellow]no --scope: this task may read or write no file "
                      "(an empty scope permits nothing). Pass --scope work to "
                      "declare one.[/]")
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


def _fmt(value, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def _print_budget(ctx, console, task_id, theme) -> None:
    m = ctx.telemetry.metrics(task_id)
    console.print(
        f"[{theme['muted']}]paid {m.paid_tokens:,} tok · free {m.free_tokens:,} tok · "
        f"${_fmt(m.cost_usd, '.4f')} · cache hit {_fmt(m.cache_hit_ratio, '.0%')} · "
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
    from .channel.telegram import recent_drops

    theme = load_theme(ctx)
    findings = verify_all(ctx.conn, ctx.log, ctx.anchor, ctx.memory)
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

    drops = recent_drops(ctx.conn, limit=10)
    if drops:
        t = Table(title="dropped channel messages (no task was created)")
        for c in ("channel", "sender", "reason", "bytes"):
            t.add_column(c, overflow="fold")
        for d in drops:
            t.add_row(d["channel"], d["sender"], d["reason"], str(d["body_len"]))
        console.print(t)
    return 1 if findings else 0


def cmd_verify(ctx, args, console) -> int:
    return cmd_alerts(ctx, args, console)


def cmd_tui(ctx, args, console) -> int:
    try:
        from .tui.app import run as run_tui
    except ImportError:
        console.print("[red]the TUI needs textual:[/red] pip install textual")
        return 1
    run_tui(ctx, args.task_id)
    return 0


def cmd_memory(ctx, args, console) -> int:
    from .memory.store import MemoryStore, check_fingerprint, forget, record_fingerprint

    if args.forget:
        removed = forget(ctx.memory, args.forget)
        for line in removed:
            console.print(f"[red]forgotten[/red] {line}")
        if not removed:
            console.print("nothing matched")
        return 0

    if args.accept:
        before = check_fingerprint(ctx.memory)
        for tier in sorted(MemoryStore.FILES):
            for e in ctx.memory.entries(tier):
                console.print(f"  [dim][{e.taint.value}][/dim] {e.text}")
        fp = record_fingerprint(ctx.memory)
        console.print(f"[green]accepted[/green] (was {before}) {fp[:16]}")
        return 0

    if args.consolidate:
        for tier in sorted(MemoryStore.FILES):
            out = ctx.memory.consolidate(tier)
            console.print(f"{tier}: {out['before']} -> {out['after']} entries, "
                          f"{out['chars']}/{out['cap']} chars")
        return 0

    for tier in sorted(MemoryStore.FILES):
        entries = ctx.memory.entries(tier)
        chars = len(ctx.memory.read_raw(tier))
        console.print(f"[bold]{tier}[/bold]  {len(entries)} entries, "
                      f"{chars}/{ctx.memory.cap(tier)} chars")
        for e in entries:
            console.print(f"  [dim][{e.taint.value}][/dim] {e.text}")
    console.print(f"[dim]{ctx.memory.root}[/dim]")
    return 0


def cmd_search(ctx, args, console) -> int:
    from .kernel import search as search_mod

    if args.rebuild:
        n = search_mod.rebuild(ctx.conn)
        console.print(f"search index rebuilt from the log: {n} rows")
        return 0

    status = search_mod.verify(ctx.conn)
    if args.verify:
        console.print_json(json.dumps(status))
        return 0 if status["consistent"] else 1
    if not status["consistent"]:
        console.print(
            f"[yellow]index has {status['indexed']} rows against "
            f"{status['expected']} expected — run `alpha search --rebuild`. "
            f"Results below may be incomplete.[/yellow]")

    if not args.query:
        console.print("nothing to search for; pass a query or --rebuild")
        return 2
    try:
        hits = search_mod.search(ctx.conn, " ".join(args.query),
                                 limit=args.limit, offset=args.offset)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return 2
    if not hits:
        console.print("no matches")
        return 0
    for h in hits:
        body = h.snippet
        if h.source == "contract":
            row = ctx.conn.execute(
                "SELECT contract_json FROM tasks WHERE id = ?", (h.ref,)
            ).fetchone()
            if row:
                body = search_mod.summarise_contract(row[0])
        console.print(f"[dim]{h.session_id[:12]}·{h.task_id[:12]} "
                      f"{h.kind}[/dim]  {body}")
    console.print(f"[dim]{len(hits)} result(s), taint=untrusted[/dim]")
    return 0


def cmd_report(ctx, args, console) -> int:
    report = run_report(ctx.conn, ctx.telemetry, args.task_id)
    out_dir = ctx.config.resolve_state("runs")
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


def _parse_drafted_from(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.lower() == "none":
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def cmd_skills(ctx, args, console) -> int:
    from .skills.gate import promotion_rate

    if args.declare_drafted_from:
        sid, raw = args.declare_drafted_from
        try:
            prov = ctx.skills.declare_drafted_from(sid, _parse_drafted_from(raw))
        except (KeyError, ValueError) as e:
            console.print(f"[red]{e}[/red]")
            return 1
        console.print(f"skill [bold]{sid[:12]}[/bold] declares drafted_from="
                      f"{prov['drafted_from']!r}")
        if prov["trials_before_declaration"]:
            console.print(f"[yellow]declared after {prov['trials_before_declaration']} "
                          "trials were recorded; that count is written beside it[/]")
        return 0

    if args.add:
        path = Path(args.add)
        if not path.exists():
            console.print(f"[red]no such file: {path}[/red]")
            return 1
        body = path.read_text(encoding="utf-8")
        provenance = {"drafted_by": "human", "source_file": str(path)}
        if args.drafted_from is not None:
            provenance["drafted_from"] = _parse_drafted_from(args.drafted_from)
        sid = ctx.skills.store(
            name=args.name or path.stem,
            body=body,
            contract={"applies_when": args.when or "(not stated)"},
            provenance=provenance,
            status="candidate",
        )
        console.print(f"stored [bold]{sid[:12]}[/bold] as a candidate")
        if args.drafted_from is None:
            console.print("[yellow]no --drafted-from: the gate refuses every paired "
                          "trial of this skill until its drafting set is declared "
                          f"(alpha skills --declare-drafted-from {sid} KEYS|none)[/]")
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

    from .control.guard import resolve_token
    from .control.server import GRACEFUL_SHUTDOWN_S, console_url, create_app

    console.print(f"control plane: {console_url(args.port, resolve_token(ctx.config.root), args.host)}")
    uvicorn.run(create_app(ctx), host=args.host, port=args.port, log_level="warning",
                timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S)
    return 0


def cmd_channel(ctx, args, console) -> int:
    from .channel.telegram import build
    from .security.origin import CHANNEL_DENIED

    cfg = ctx.config.channels.telegram
    if not cfg.enabled:
        console.print("channels.telegram.enabled is false. Nothing started.")
        return 2
    if not cfg.allowed_senders:
        console.print(
            "channels.telegram.allowed_senders is empty, which means NOBODY. "
            "Add your own Telegram user id before starting the bot.")
        return 2
    if not os.environ.get(cfg.token_env):
        console.print(
            f"no bot token: set {cfg.token_env}. It must not go in config.yaml.")
        return 2

    bot = build(ctx)
    console.print(
        f"telegram bot polling; {len(cfg.allowed_senders)} allowed sender(s); "
        f"channel tasks are denied {', '.join(sorted(CHANNEL_DENIED))}")
    bot.serve_forever()
    return 0


def cmd_snapshot(ctx, args, console) -> int:
    from .kernel.db import snapshot

    out = snapshot(ctx.conn, args.dest)
    console.print(f"snapshot written to {out} (WAL-safe, via the backup API)")
    return 0


def cmd_lineage(ctx, args, console) -> int:
    from .kernel import sessions

    rows = sessions.for_task(ctx.conn, args.task_id)
    if not rows:
        console.print("no sessions for that task")
        return 1
    for r in rows:
        chain = " -> ".join(s[:10] for s in sessions.lineage(ctx.conn, r["id"]))
        console.print(f"{r['id']}  {r['status']:<6} {r['opened_reason']:<8} {chain}")
    return 0


def cmd_schedule(ctx, args, console) -> int:
    from . import schedule as sched

    if args.action == "add":
        if not args.spec or not args.goal:
            console.print('usage: alpha schedule add "<cron>" "<goal>"')
            return 2
        lim = ctx.config.limits
        sid = sched.add(ctx.conn, args.spec,
                        TaskContract(goal=args.goal,
                                     artifact_scope=args.scope or ["work"]),
                        Budget(usd=args.budget_usd or lim.default_task_budget_usd,
                               seconds=args.budget_seconds
                               or lim.default_task_budget_seconds))
        nxt = sched.next_fire(ctx.conn, sched.get(ctx.conn, sid))
        console.print(f"{sid}  {args.spec}  next "
                      f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(nxt))}")
        return 0
    if args.action == "remove":
        ok = sched.remove(ctx.conn, args.id or "")
        console.print("removed" if ok else "no such active schedule")
        return 0 if ok else 1
    rows = sched.active(ctx.conn)
    if not rows:
        console.print("no schedules")
    for s in rows:
        nxt = sched.next_fire(ctx.conn, s)
        goal = json.loads(s["contract_json"])["goal"]
        console.print(f"{s['id']}  {s['spec']:<16} next "
                      f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(nxt))}  "
                      f"{goal[:50]}")
    return 0


def cmd_supervise(ctx, args, console) -> int:
    from . import schedule as sched

    console.print(f"supervising {len(sched.active(ctx.conn))} schedule(s), "
                  f"waking every {args.tick:g}s")
    sched.supervise(ctx, tick_s=args.tick, once=args.once,
                    report=lambda r: console.print(r))
    return 0


def cmd_notifications(ctx, args, console) -> int:
    from . import schedule as sched

    rows = sched.notifications(ctx.conn, unseen_only=not args.all)
    if not rows:
        console.print("nothing new")
    for n in rows:
        d = n["detail"]
        console.print(f"{n['id']}  {n['kind']:<7} {n['task_id'][:12]}  "
                      f"{d.get('tool', '')}  {d.get('preview', '')[:60]}  "
                      f"-> alpha approvals --decide {d.get('approval_id', '')}")
    if args.seen:
        sched.mark_seen(ctx.conn, [n["id"] for n in rows])
    return 0


def cmd_monitor(ctx, args, console) -> int:
    from .assure.monitor import run_monitor

    n = run_monitor(ctx, args.task_id)
    console.print(f"{n} finding(s) written to monitor_flags (agent never sees them)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alpha", description="alpha test 1")
    from .paths import default_config_path

    p.add_argument("--config",
                   default=str(default_config_path() or "config.yaml"))
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

    tui = sub.add_parser("tui", help="terminal UI: live output, tools, budget")
    tui.add_argument("task_id", nargs="?", default=None)
    tui.set_defaults(fn=cmd_tui)

    mem = sub.add_parser("memory", help="show or correct persistent memory")
    mem.add_argument("--forget", help="remove entries containing this text")
    mem.add_argument("--consolidate", action="store_true")
    mem.add_argument("--accept", action="store_true",
                     help="bless the files as they are now (after a hand edit)")
    mem.set_defaults(fn=cmd_memory)

    sr = sub.add_parser("search", help="search goals and answers from earlier runs")
    sr.add_argument("query", nargs="*")
    sr.add_argument("--limit", type=int, default=10)
    sr.add_argument("--offset", type=int, default=0)
    sr.add_argument("--rebuild", action="store_true",
                    help="rebuild the index from the log; the log always wins")
    sr.add_argument("--verify", action="store_true",
                    help="report index/log agreement without changing either")
    sr.set_defaults(fn=cmd_search)

    rp = sub.add_parser("report", help="write the per-run JSON and CSV")
    rp.add_argument("task_id")
    rp.set_defaults(fn=cmd_report)

    pa = sub.add_parser("pareto", help="cost vs pass rate across arms")
    pa.set_defaults(fn=cmd_pareto)

    sk = sub.add_parser("skills", help="list skills, add one, run the gate or decay")
    sk.add_argument("--add", metavar="SKILL.md", help="store a hand-written skill as a candidate")
    sk.add_argument("--name", help="skill name (defaults to the filename)")
    sk.add_argument("--when", help="one line: when this skill applies")
    sk.add_argument("--drafted-from", metavar="KEYS",
                    help="with --add: suite task keys the skill was drafted from, "
                         "comma-separated, or 'none'. Undeclared, the gate refuses "
                         "every paired trial of it")
    sk.add_argument("--declare-drafted-from", nargs=2, metavar=("SKILL_ID", "KEYS"),
                    help="declare an existing skill's drafting set - once; a "
                         "declaration is never overwritten")
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

    sc = sub.add_parser("schedule", help="add, list or remove a scheduled task")
    sc.add_argument("action", choices=["add", "list", "remove"])
    sc.add_argument("spec", nargs="?",
                    help="cron: minute hour day-of-month month day-of-week")
    sc.add_argument("goal", nargs="?")
    sc.add_argument("--id")
    sc.add_argument("--scope", nargs="*")
    sc.add_argument("--budget-usd", type=float)
    sc.add_argument("--budget-seconds", type=int)
    sc.set_defaults(fn=cmd_schedule)

    sp = sub.add_parser("supervise", help="fire scheduled tasks as they fall due")
    sp.add_argument("--tick", type=float, default=30.0)
    sp.add_argument("--once", action="store_true", help="one wake, then exit")
    sp.set_defaults(fn=cmd_supervise)

    nt = sub.add_parser("notifications", help="what parked while nobody watched")
    nt.add_argument("--all", action="store_true")
    nt.add_argument("--seen", action="store_true", help="mark them seen")
    nt.set_defaults(fn=cmd_notifications)

    lin = sub.add_parser("lineage", help="a task's sessions, parent to child")
    lin.add_argument("task_id")
    lin.set_defaults(fn=cmd_lineage)

    snp = sub.add_parser("snapshot", help="copy the database as evidence, WAL-safe")
    snp.add_argument("dest", help="where to write the copy; never overwritten")
    snp.set_defaults(fn=cmd_snapshot)

    ch = sub.add_parser("channel", help="run the Telegram bot (long poll)")
    ch.set_defaults(fn=cmd_channel)

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
    except Exception as e:  # noqa: BLE001
        console.print(f"[bold red]{type(e).__name__}[/bold red]: {e}")
        return 1
    finally:
        ctx.close()


if __name__ == "__main__":
    sys.exit(main())
