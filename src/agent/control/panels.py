"""The Control Center's panes (iteration 3, 4.3), over what already exists.

Reads, and one write: blessing tier-1 memory (`alpha memory --accept`), the
deliberate act that makes a hand edit legitimate. Every pane shows the SAME
records the CLI shows; none is a second source of truth.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path


def memory(ctx) -> dict:
    from ..memory.store import MemoryStore, check_fingerprint

    m = ctx.memory
    tiers = []
    for tier in sorted(MemoryStore.FILES):
        tiers.append({"tier": tier, "file": MemoryStore.FILES[tier][0],
                      "chars": len(m.read_raw(tier)), "cap": m.cap(tier),
                      "entries": [{"taint": e.taint.value, "text": e.text}
                                  for e in m.entries(tier)]})
    return {"root": str(m.root), "blessing": check_fingerprint(m), "tiers": tiers}


def accept_memory(ctx) -> dict:
    from ..memory.store import check_fingerprint, record_fingerprint

    before = check_fingerprint(ctx.memory)
    fp = record_fingerprint(ctx.memory)
    return {"before": before, "blessing": check_fingerprint(ctx.memory), "fingerprint": fp}


def schedules(ctx) -> dict:
    from .. import schedule as sched

    rows = []
    for s in sched.active(ctx.conn):
        missed = ctx.conn.execute(
            "SELECT COUNT(*) FROM schedule_firings WHERE schedule_id=? AND outcome='missed'",
            (s["id"],)).fetchone()[0]
        rows.append({"id": s["id"], "spec": s["spec"],
                     "goal": json.loads(s["contract_json"]).get("goal", ""),
                     "next_fire": sched.next_fire(ctx.conn, s), "missed": missed})
    return {"schedules": rows, "parked": sched.notifications(ctx.conn, unseen_only=False)[:50]}


def channels(ctx) -> dict:
    """The allowlist, and the drop log as hash and length only - never a
    stranger's text, which is untrusted and was never meant to be kept."""
    from ..channel.telegram import recent_drops

    tg = ctx.config.channels.telegram
    return {"telegram": {"enabled": tg.enabled, "allowed_senders": list(tg.allowed_senders)},
            "drops": recent_drops(ctx.conn, limit=50)}


def integrity(ctx) -> dict:
    from ..kernel.integrity import verify_all

    anchor = Path(ctx.anchor.path)
    lines = anchor.read_text(encoding="utf-8").splitlines() if anchor.exists() else []
    return {"findings": verify_all(ctx.conn, ctx.log, ctx.anchor, ctx.memory),
            "anchor": {"path": str(anchor), "entries": len(lines),
                       "last_written": anchor.stat().st_mtime if anchor.exists() else None},
            "tasks": ctx.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]}


def measurement(ctx) -> dict:
    """Versions, run records, and the 1.25 inventory against THIS database -
    when bench/ is beside the source (a checkout); an installed copy has none."""
    from .. import HARNESS_VERSION, TOOLSET_VERSION

    runs = ctx.config.resolve_state("runs")
    records = sorted(({"name": p.name, "bytes": p.stat().st_size, "modified": p.stat().st_mtime}
                      for p in runs.glob("*") if p.is_file()),
                     key=lambda r: -r["modified"])[:100] if runs.is_dir() else []
    inv = Path(__file__).resolve().parents[3] / "bench" / "inventory.py"
    inventory = None
    if inv.is_file():
        spec = importlib.util.spec_from_file_location("alpha_inventory", inv)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        inventory = mod.take(ctx.config.resolve_state(ctx.config.db_path))
    return {"harness_version": HARNESS_VERSION, "toolset_version": TOOLSET_VERSION,
            "profile": ctx.config.profile, "provenance": ctx.config.profile_provenance,
            "runs": records, "inventory": inventory}


def settings(ctx) -> dict:
    c = ctx.config
    return {"profile": c.profile,
            "backends": [{"name": n, "kind": b.cfg.kind, "model": b.cfg.model,
                          "tier": b.cfg.trust_tier, "window": b.cfg.budgeted_window}
                         for n, b in sorted(ctx.backends.items())],
            "limits": json.loads(c.limits.model_dump_json()),
            "autonomy": json.loads(c.autonomy.model_dump_json()),
            "mechanisms": json.loads(c.mechanisms.model_dump_json()),
            "root": c.root, "state_dir": c.state_dir or c.root}


def logs(ctx, limit: int = 200) -> dict:
    rows = ctx.conn.execute(
        "SELECT task_id, seq, type, created_at FROM events ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 1000)),)).fetchall()
    return {"events": [{"task_id": r[0], "seq": r[1], "type": r[2], "at": r[3]} for r in rows],
            "now": time.time()}


def context_meter(ctx, task_id: str | None = None) -> dict:
    """What the context ring shows: the model's window AND the compaction
    ceiling, because the ceiling is what governs behaviour (enforce_ceiling
    compacts or delegates there, well before the window is full)."""
    window = ctx.window_for("cloud")
    ratio = ctx.config.limits.context_fill_ratio
    used = 0
    if task_id:
        r = ctx.conn.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND type='model.called' "
            "ORDER BY seq DESC LIMIT 1", (task_id,)).fetchone()
        if r:
            used = int(json.loads(r[0]).get("est_input_tokens") or 0)
    return {"window": window, "fill_ratio": ratio, "ceiling": int(window * ratio),
            "used": used, "estimated": True}
