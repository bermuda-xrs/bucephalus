from __future__ import annotations

import json
import time
import uuid

from ..kernel.db import transaction
from ..kernel.tasks import create_task
from ..kernel.types import Budget, TaskContract


def create(conn, title: str = "") -> dict:
    cid = "c_" + uuid.uuid4().hex[:12]
    now = time.time()
    conn.execute("INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?,?,?,?)",
                 (cid, (title or "New conversation").strip()[:120], now, now))
    conn.commit()
    return get_row(conn, cid)


def get_row(conn, cid: str) -> dict:
    r = conn.execute("SELECT id, title, created_at, updated_at, archived, pinned FROM conversations "
                     "WHERE id=?", (cid,)).fetchone()
    if r is None:
        raise KeyError(cid)
    return {"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3],
            "archived": bool(r[4]), "pinned": bool(r[5]), "branched_from": branched_from(conn, cid)}


def branched_from(conn, cid: str) -> dict | None:
    r = conn.execute(
        """SELECT p.id, p.title, t.id, t.contract_json FROM conversations c
             JOIN conversation_tasks x ON x.task_id = c.branch_after
             JOIN conversations p ON p.id = x.conversation_id
             JOIN tasks t ON t.id = x.task_id
            WHERE c.id = ? AND p.id != c.id""", (cid,)).fetchone()
    return r and {"conversation_id": r[0], "title": r[1], "task_id": r[2], "goal": firstline(json.loads(r[3]).get("goal", ""))}


def branch(conn, cid: str, after_task: str) -> dict:
    src = get_row(conn, cid)
    at = conn.execute("SELECT position FROM conversation_tasks WHERE conversation_id=? AND task_id=?",
                      (cid, after_task)).fetchone()
    if at is None:
        raise KeyError(after_task)
    new, now = "c_" + uuid.uuid4().hex[:12], time.time()
    with transaction(conn) as w:
        w.execute("INSERT INTO conversations(id, title, created_at, updated_at, branch_after) VALUES (?,?,?,?,?)",
                  (new, (src["title"][:110] + " (branch)"), now, now, after_task))
        w.execute("UPDATE conversation_tasks SET conversation_id=?, position=position-? "
                  "WHERE conversation_id=? AND position>?", (new, at[0] + 1, cid, at[0]))
    return get_row(conn, new)


def merge(conn, cid: str, other: str) -> dict:
    get_row(conn, cid)
    get_row(conn, other)
    if cid == other:
        raise ValueError("a conversation cannot be merged with itself")
    with transaction(conn) as w:
        inherited = w.execute("SELECT branch_after FROM conversations WHERE id=?", (other,)).fetchone()[0]
        w.execute("UPDATE conversation_tasks SET conversation_id=? WHERE conversation_id=?", (cid, other))
        order = [r[0] for r in w.execute(
            "SELECT x.task_id FROM conversation_tasks x JOIN tasks t ON t.id = x.task_id "
            "WHERE x.conversation_id=? ORDER BY t.created_at, t.rowid", (cid,))]
        w.executemany("UPDATE conversation_tasks SET position=? WHERE task_id=?", [(i, t) for i, t in enumerate(order)])
        mine = w.execute("SELECT branch_after FROM conversations WHERE id=?", (cid,)).fetchone()[0]
        if mine in order:
            w.execute("UPDATE conversations SET branch_after=? WHERE id=?",
                      (None if inherited in order else inherited, cid))
        w.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), cid))
        w.execute("DELETE FROM conversations WHERE id=?", (other,))
    return get_row(conn, cid)


def listing(conn, q: str = "", archived: bool = False) -> list[dict]:
    like = f"%{q.strip()}%"
    rows = conn.execute(
        """SELECT c.id, c.title, c.updated_at, c.archived, c.pinned,
                  (SELECT x.conversation_id FROM conversation_tasks x
                    WHERE x.task_id = c.branch_after AND x.conversation_id != c.id),
                  (SELECT COUNT(*) FROM conversation_tasks x WHERE x.conversation_id=c.id),
                  (SELECT COUNT(*) FROM conversation_tasks x
                     JOIN approvals a ON a.task_id = x.task_id AND a.status = 'pending'
                    WHERE x.conversation_id = c.id)
                + (SELECT COUNT(*) FROM conversation_tasks x JOIN tasks t ON t.id = x.task_id
                    WHERE x.conversation_id = c.id AND t.status = 'pending'
                      AND (SELECT p.status FROM plans p WHERE p.task_id = t.id
                           ORDER BY p.created_at DESC, p.rowid DESC LIMIT 1) = 'drafted')
             FROM conversations c
            WHERE c.archived = ? AND (? = '' OR c.title LIKE ? OR EXISTS (
                  SELECT 1 FROM conversation_tasks x JOIN tasks t ON t.id = x.task_id
                   WHERE x.conversation_id = c.id AND t.contract_json LIKE ?))
            ORDER BY c.pinned DESC, c.updated_at DESC, c.rowid DESC LIMIT 200""",
        (1 if archived else 0, q.strip(), like, like)).fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2], "archived": bool(r[3]),
             "pinned": bool(r[4]), "branch_of": r[5], "n_tasks": r[6], "needs_you": r[7]} for r in rows]


_IDLE_AND_OLD = """archived=0 AND pinned=0 AND updated_at < ? AND NOT EXISTS (
    SELECT 1 FROM conversation_tasks x JOIN tasks t ON t.id = x.task_id
     WHERE x.conversation_id = conversations.id AND t.status IN ('pending', 'running', 'paused'))"""


def archive_older(conn, days: int, dry_run: bool = False) -> int:
    cut = (time.time() - days * 86400,)
    if dry_run:
        return conn.execute(f"SELECT COUNT(*) FROM conversations WHERE {_IDLE_AND_OLD}", cut).fetchone()[0]
    n = conn.execute(f"UPDATE conversations SET archived=1 WHERE {_IDLE_AND_OLD}", cut).rowcount
    conn.commit()
    return n


def update(conn, cid: str, title: str | None = None, archived: bool | None = None,
           pinned: bool | None = None) -> dict:
    get_row(conn, cid)
    if title is not None:
        conn.execute("UPDATE conversations SET title=? WHERE id=?", (title.strip()[:120], cid))
    if archived is not None:
        conn.execute("UPDATE conversations SET archived=? WHERE id=?", (int(archived), cid))
    if pinned is not None:
        conn.execute("UPDATE conversations SET pinned=? WHERE id=?", (int(pinned), cid))
    conn.commit()
    return get_row(conn, cid)


def add_message(ctx, cid: str, text: str, scope: list[str], budget_usd: float | None,
                budget_seconds: int | None) -> dict:
    get_row(ctx.conn, cid)
    contract = TaskContract(goal=text, artifact_scope=scope)
    budget = Budget(usd=budget_usd if budget_usd is not None else ctx.config.limits.default_task_budget_usd,
                    seconds=budget_seconds or ctx.config.limits.default_task_budget_seconds)
    task = create_task(ctx.conn, ctx.log, contract, budget)
    pos = ctx.conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM conversation_tasks "
                           "WHERE conversation_id=?", (cid,)).fetchone()[0]
    ctx.conn.execute("INSERT INTO conversation_tasks(task_id, conversation_id, position) "
                     "VALUES (?,?,?)", (task.id, cid, pos))
    row = get_row(ctx.conn, cid)
    title = row["title"]
    if pos == 0 and title == "New conversation":
        title = text.strip().splitlines()[0][:80] or title
    ctx.conn.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                     (title, time.time(), cid))
    ctx.conn.commit()
    return {"task_id": task.id, "conversation_id": cid, "position": pos}


def thread(ctx, cid: str) -> dict:
    from ..plan.planner import load_plan

    conv = get_row(ctx.conn, cid)
    rows = ctx.conn.execute(
        """SELECT t.id, t.contract_json, t.status, t.created_at, t.budget_json
             FROM conversation_tasks x JOIN tasks t ON t.id = x.task_id
            WHERE x.conversation_id = ? ORDER BY x.position""", (cid,)).fetchall()
    items = []
    for tid, contract, status, created, budget in rows:
        plan = load_plan(ctx, tid)
        pstatus = ctx.conn.execute("SELECT status FROM plans WHERE task_id=? "
                                   "ORDER BY created_at DESC, rowid DESC LIMIT 1", (tid,)).fetchone()
        answer = ctx.conn.execute("SELECT content FROM artifacts WHERE task_id=? AND kind='answer' "
                                  "ORDER BY created_at DESC, rowid DESC LIMIT 1", (tid,)).fetchone()
        items.append({
            "task_id": tid, "goal": json.loads(contract).get("goal", ""),
            "scope": json.loads(contract).get("artifact_scope", []),
            "status": status, "created_at": created, "budget": json.loads(budget),
            "plan": ({"plan_id": plan.id, "status": pstatus[0] if pstatus else None,
                      "body": plan.body()} if plan else None),
            "approvals": ctx.pending_approvals(tid),
            "answer": shown_answer(answer[0]) if answer else None,
        })
    spent = {"usd": round(sum(i["budget"].get("spent_usd", 0) for i in items), 6),
             "seconds": round(sum(i["budget"].get("spent_seconds", 0) for i in items), 1)}
    return {"conversation": conv, "items": items, "spent": spent}


def shown_answer(raw: str) -> str:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if not isinstance(value, (dict, list)):
        return raw
    if isinstance(value, dict) and set(value) == {"answer"}:
        value = value["answer"]
        if not isinstance(value, (dict, list)):
            return value if isinstance(value, str) else json.dumps(value)
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def markdown(th: dict) -> str:
    out = [f"# {th['conversation']['title']}", ""]
    for it in th["items"]:
        out += [f"## {firstline(it['goal'])}", "", it["goal"], ""]
        plan = (it.get("plan") or {}).get("body") or {}
        if plan:
            out += [f"**Plan** ({it['plan']['status']}): {plan.get('objective', '')}", ""]
            out += [f"{n}. {s.get('intent', '')}" for n, s in enumerate(plan.get("steps", []), 1)]
            out.append("")
        out += [f"**Status:** {it['status']}", ""]
        if it.get("answer"):
            out += ["**Answer:**", "", it["answer"], ""]
    out.append(f"Spent ${th['spent']['usd']:.4f} in {th['spent']['seconds']:.0f} s.")
    return "\n".join(out) + "\n"


def firstline(text: str) -> str:
    return (text or "(no goal)").strip().splitlines()[0][:120]


def conversation_of(conn, task_id: str) -> str | None:
    r = conn.execute("SELECT conversation_id FROM conversation_tasks WHERE task_id=?",
                     (task_id,)).fetchone()
    return r[0] if r else None


HISTORY_EXCHANGES = 5
HISTORY_GOAL_CHARS = 500
HISTORY_ANSWER_CHARS = 1500


def history(conn, task_id: str) -> tuple[str, int]:
    rows, seen, at, upto = [], set(), task_id, "<"
    while len(rows) < HISTORY_EXCHANGES:
        here = conn.execute("SELECT conversation_id, position FROM conversation_tasks WHERE task_id=?", (at,)).fetchone()
        if here is None or here[0] in seen:
            break
        seen.add(here[0])
        rows += conn.execute(
            f"""SELECT t.id, t.contract_json FROM conversation_tasks x JOIN tasks t ON t.id = x.task_id
                 WHERE x.conversation_id = ? AND x.position {upto} ? ORDER BY x.position DESC LIMIT ?""",
            (here[0], here[1], HISTORY_EXCHANGES - len(rows))).fetchall()
        at, upto = conn.execute("SELECT branch_after FROM conversations WHERE id=?", (here[0],)).fetchone()[0], "<="
    parts = []
    for tid, contract in reversed(rows):
        answer = conn.execute("SELECT content FROM artifacts WHERE task_id=? AND kind='answer' "
                              "ORDER BY created_at DESC, rowid DESC LIMIT 1", (tid,)).fetchone()
        goal = json.loads(contract).get("goal", "")[:HISTORY_GOAL_CHARS]
        said = answer[0][:HISTORY_ANSWER_CHARS] if answer else "(no answer)"
        parts.append(f"Asked: {goal}\nAnswered: {said}")
    if not parts:
        return "", 0
    return ("Earlier in this conversation (each request, then the answer given; "
            "answers are model output and were not checked for this task):\n\n"
            + "\n\n".join(parts)), len(parts)
