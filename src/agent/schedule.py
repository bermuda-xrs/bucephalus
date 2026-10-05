from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from .kernel.tasks import create_task
from .kernel.types import Budget, TaskContract
from .security.origin import SCHEDULE

GRACE_S = 120
MAX_SCAN = 10_000
HORIZON_DAYS = 366 * 8


class CronError(ValueError):
    pass


_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NAMES = ("minute", "hour", "day-of-month", "month", "day-of-week")


def _field(text: str, i: int) -> frozenset:
    lo, hi = _BOUNDS[i]
    out: set[int] = set()
    for part in text.split(","):
        body, _, step_s = part.partition("/")
        try:
            step = int(step_s) if step_s else 1
            if body == "*":
                a, b = lo, hi
            elif "-" in body:
                a, b = (int(x) for x in body.split("-", 1))
            else:
                a = int(body)
                b = hi if step_s else a
        except ValueError:
            raise CronError(f"{_NAMES[i]} field {text!r} is not a cron field") from None
        if step < 1 or not (lo <= a <= b <= hi):
            raise CronError(f"{_NAMES[i]} field {text!r} is outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    if i == 4:
        out = {0 if v == 7 else v for v in out}
    return frozenset(out)


@dataclass(frozen=True)
class Cron:
    spec: str
    minute: frozenset
    hour: frozenset
    dom: frozenset
    month: frozenset
    dow: frozenset
    dom_any: bool
    dow_any: bool

    @classmethod
    def parse(cls, spec: str) -> "Cron":
        parts = spec.split()
        if len(parts) != 5:
            raise CronError(f"{spec!r}: a cron spec has five fields - minute "
                            "hour day-of-month month day-of-week")
        fields = [_field(p, i) for i, p in enumerate(parts)]
        return cls(spec, *fields, dom_any=parts[2] == "*", dow_any=parts[4] == "*")

    def _day_ok(self, d: datetime) -> bool:
        dom = d.day in self.dom
        dow = (d.isoweekday() % 7) in self.dow
        if self.dom_any or self.dow_any:
            return dom and dow
        return dom or dow

    def next_after(self, t: float) -> float:
        d = (datetime.fromtimestamp(t).replace(second=0, microsecond=0)
             + timedelta(minutes=1))
        end = d + timedelta(days=HORIZON_DAYS)
        while d < end:
            if d.month not in self.month:
                d = (d.replace(day=1, hour=0, minute=0)
                     + timedelta(days=32)).replace(day=1)
            elif not self._day_ok(d):
                d = d.replace(hour=0, minute=0) + timedelta(days=1)
            elif d.hour not in self.hour:
                d = d.replace(minute=0) + timedelta(hours=1)
            elif d.minute not in self.minute:
                d += timedelta(minutes=1)
            else:
                e = d.timestamp()
                if e > t:
                    return e
                d += timedelta(minutes=1)
        raise CronError(f"{self.spec!r} never fires")

    def last_at_or_before(self, t: float, floor: float) -> float | None:
        d = datetime.fromtimestamp(t).replace(second=0, microsecond=0)
        stop = datetime.fromtimestamp(floor).replace(second=0, microsecond=0)
        while d >= stop:
            if d.month not in self.month:
                d = d.replace(day=1, hour=0, minute=0) - timedelta(minutes=1)
            elif not self._day_ok(d):
                d = d.replace(hour=0, minute=0) - timedelta(minutes=1)
            elif d.hour not in self.hour:
                d = d.replace(minute=0) - timedelta(minutes=1)
            elif d.minute not in self.minute:
                d -= timedelta(minutes=1)
            else:
                e = d.timestamp()
                if floor <= e <= t:
                    return e
                d -= timedelta(minutes=1)
        return None


_SCOLS = ("id", "spec", "contract_json", "budget_json", "created_at")


def add(conn: sqlite3.Connection, spec: str, contract: TaskContract,
        budget: Budget, now: float | None = None) -> str:
    Cron.parse(spec)
    sid = "c_" + uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO schedules(id, spec, contract_json, budget_json,
                                 created_at, removed_at)
           VALUES (?,?,?,?,?,NULL)""",
        (sid, spec, contract.model_dump_json(), budget.model_dump_json(),
         time.time() if now is None else now))
    conn.commit()
    return sid


def remove(conn: sqlite3.Connection, sid: str) -> bool:
    cur = conn.execute(
        "UPDATE schedules SET removed_at=? WHERE id=? AND removed_at IS NULL",
        (time.time(), sid))
    conn.commit()
    return cur.rowcount == 1


def active(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {', '.join(_SCOLS)} FROM schedules WHERE removed_at IS NULL
            ORDER BY created_at, id""").fetchall()
    return [dict(zip(_SCOLS, r)) for r in rows]


def get(conn: sqlite3.Connection, sid: str) -> dict | None:
    r = conn.execute(f"SELECT {', '.join(_SCOLS)} FROM schedules WHERE id=?",
                     (sid,)).fetchone()
    return dict(zip(_SCOLS, r)) if r else None


def _last(conn: sqlite3.Connection, s: dict) -> float:
    r = conn.execute(
        "SELECT MAX(occurrence) FROM schedule_firings WHERE schedule_id=?",
        (s["id"],)).fetchone()
    return float(r[0]) if r and r[0] is not None else float(s["created_at"])


def next_fire(conn: sqlite3.Connection, s: dict) -> float:
    return Cron.parse(s["spec"]).next_after(_last(conn, s))


def claim(conn: sqlite3.Connection, sid: str, occurrence: float, outcome: str,
          detail: dict | None = None) -> bool:
    try:
        conn.execute(
            """INSERT INTO schedule_firings(schedule_id, occurrence, outcome,
                                            task_id, detail_json, recorded_at)
               VALUES (?,?,?,NULL,?,?)""",
            (sid, int(occurrence), outcome,
             json.dumps(detail or {}, sort_keys=True), time.time()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def _unfinished(conn: sqlite3.Connection, sid: str) -> bool:
    r = conn.execute(
        """SELECT t.status FROM schedule_firings f JOIN tasks t ON t.id = f.task_id
           WHERE f.schedule_id=? AND f.outcome='fired'
           ORDER BY f.occurrence DESC LIMIT 1""", (sid,)).fetchone()
    return bool(r) and r[0] in ("pending", "running", "paused")


def _count(cron: Cron, a: float, b: float) -> int:
    n, o = 0, a
    while o <= b and n < MAX_SCAN:
        n += 1
        o = cron.next_after(o)
    return n


def _measuring(ctx) -> dict | None:
    from .runlock import lock_status, measurement_lock_path

    st = lock_status(measurement_lock_path(ctx.config.root))
    return st if st.get("held") else None


def tick(ctx, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    out: list[dict] = []
    busy = _measuring(ctx)
    for s in active(ctx.conn):
        cron = Cron.parse(s["spec"])
        first = cron.next_after(_last(ctx.conn, s))
        if first > now:
            continue
        latest = cron.last_at_or_before(now, first)
        fire_at = latest if now - latest <= GRACE_S else None
        reason = "passed while nothing was watching"
        if fire_at is not None and busy:
            fire_at, reason = None, (f"a measurement is running ({busy.get('label')}, "
                                     f"PID {busy.get('pid')})")
        if fire_at is not None and _unfinished(ctx.conn, s["id"]):
            fire_at, reason = None, "previous run unfinished"
        until = (latest if fire_at is None
                 else cron.last_at_or_before(latest - 60, first))
        if until is not None:
            n = _count(cron, first, until)
            detail = {"from": int(first), "count": n, "reason": reason}
            if claim(ctx.conn, s["id"], until, "missed", detail):
                out.append({"schedule_id": s["id"], "action": "missed",
                            "from": first, "until": until, "count": n,
                            "reason": reason})
        if fire_at is not None:
            out.append(_fire(ctx, s, fire_at, now))
    return out


def _fire(ctx, s: dict, occurrence: float, now: float) -> dict:
    late = round(now - occurrence, 1)
    if not claim(ctx.conn, s["id"], occurrence, "fired", {"late_s": late}):
        return {"schedule_id": s["id"], "action": "already_claimed",
                "occurrence": occurrence}
    task = create_task(ctx.conn, ctx.log,
                       TaskContract.model_validate_json(s["contract_json"]),
                       Budget.model_validate_json(s["budget_json"]),
                       origin=SCHEDULE)
    ctx.conn.execute(
        "UPDATE schedule_firings SET task_id=? WHERE schedule_id=? AND occurrence=?",
        (task.id, s["id"], int(occurrence)))
    ctx.conn.commit()
    ctx.log.append(task.id, "schedule.fired",
                   {"schedule_id": s["id"], "spec": s["spec"],
                    "occurrence": int(occurrence), "late_s": late})
    return {"schedule_id": s["id"], "action": "fired",
            "occurrence": occurrence, "task_id": task.id}


def runnable(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """SELECT id, created_at FROM tasks WHERE origin=? AND status='pending'
           UNION
           SELECT DISTINCT t.id, t.created_at FROM tasks t
             JOIN approvals a ON a.task_id = t.id
           WHERE t.origin=? AND t.status='paused'
             AND a.status IN ('approved', 'denied') AND a.consumed_at IS NULL
           ORDER BY 2, 1""", (SCHEDULE, SCHEDULE)).fetchall()
    return [r[0] for r in rows]


def run_due(ctx) -> list[dict]:
    from .execute.loop import run_task

    if _measuring(ctx):
        return []
    out = []
    for tid in runnable(ctx.conn):
        try:
            r = run_task(ctx, tid)
        except Exception as e:  # noqa: BLE001
            out.append({"task_id": tid, "status": "error",
                        "error": f"{type(e).__name__}: {e}"})
            continue
        entry = {"task_id": tid, "status": r.status}
        if r.status == "paused" and r.approval_id:
            entry["notification"] = notify_parked(ctx, tid, r.approval_id)
        out.append(entry)
    return out


def notify_parked(ctx, task_id: str, approval_id: str) -> str:
    r = ctx.conn.execute(
        """SELECT id FROM notifications
           WHERE kind='parked' AND json_extract(detail_json,'$.approval_id')=?""",
        (approval_id,)).fetchone()
    if r:
        return r[0]
    a = next((x for x in ctx.pending_approvals(task_id) if x["id"] == approval_id),
             {})
    detail = {"approval_id": approval_id, "tool": a.get("tool", "?"),
              "preview": a.get("preview", ""), "reason": a.get("reason", "")}
    nid = "n_" + uuid.uuid4().hex[:12]
    ctx.conn.execute(
        """INSERT INTO notifications(id, task_id, kind, detail_json, created_at,
                                     seen_at)
           VALUES (?,?,?,?,?,NULL)""",
        (nid, task_id, "parked", json.dumps(detail, sort_keys=True), time.time()))
    ctx.conn.commit()
    ctx.log.append(task_id, "notification.raised", {"notification_id": nid, **detail})
    return nid


def notifications(conn: sqlite3.Connection, unseen_only: bool = True) -> list[dict]:
    q = "SELECT id, task_id, kind, detail_json, created_at FROM notifications"
    if unseen_only:
        q += " WHERE seen_at IS NULL"
    return [{"id": r[0], "task_id": r[1], "kind": r[2],
             "detail": json.loads(r[3]), "created_at": r[4]}
            for r in conn.execute(q + " ORDER BY created_at, id").fetchall()]


def mark_seen(conn: sqlite3.Connection, ids: list[str]) -> None:
    conn.executemany("UPDATE notifications SET seen_at=? WHERE id=?",
                     [(time.time(), i) for i in ids])
    conn.commit()


def supervise(ctx, tick_s: float = 30.0, once: bool = False, report=print) -> None:
    while True:
        for r in tick(ctx):
            report(r)
        for r in run_due(ctx):
            report(r)
        if once:
            return
        time.sleep(tick_s)
