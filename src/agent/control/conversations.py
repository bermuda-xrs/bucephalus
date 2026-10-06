from __future__ import annotations

import json
import time
import uuid

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
            "archived": bool(r[4]), "pinned": bool(r[5])}


def listing(conn, q: str = "", archived: bool = False) -> list[dict]:
    like = f"%{q.strip()}%"
    rows = conn.execute(
        """SELECT c.id, c.title, c.updated_at, c.archived, c.pinned,
                  (SELECT COUNT(*) FROM conversation_tasks x WHERE x.conversation_id=c.id),
                  (SELECT COUNT(*) FROM conversation_tasks x
                     JOIN approvals a ON a.task_id = x.task_id AND a.status = 'pending'
                    WHERE x.conversation_id = c.id)
                + (SELECT COUNT(*) FROM conversation_tasks x JOIN tasks t ON t.id = x.task_id
                    WHERE x.conversation_id = c.id AND t.status = 'pending'
                      AND (SELECT p.status FROM plans p WHERE p.task_id = t.id
                           ORDER BY p.created_at DESC LIMIT 1) = 'drafted')
             FROM conversations c
            WHERE c.archived = ? AND (? = '' OR c.title LIKE ? OR EXISTS (
                  SELECT 1 FROM conversation_tasks x JOIN tasks t ON t.id = x.task_id
                   WHERE x.conversation_id = c.id AND t.contract_json LIKE ?))
            ORDER BY c.pinned DESC, c.updated_at DESC LIMIT 200""",
        (1 if archived else 0, q.strip(), like, like)).fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2], "archived": bool(r[3]),
             "pinned": bool(r[4]), "n_tasks": r[5], "needs_you": r[6]} for r in rows]


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
                                   "ORDER BY created_at DESC LIMIT 1", (tid,)).fetchone()
        answer = ctx.conn.execute("SELECT content FROM artifacts WHERE task_id=? AND kind='answer' "
                                  "ORDER BY created_at DESC LIMIT 1", (tid,)).fetchone()
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
    rows = conn.execute(
        """SELECT t.id, t.contract_json FROM conversation_tasks me
             JOIN conversation_tasks x ON x.conversation_id = me.conversation_id
                                      AND x.position < me.position
             JOIN tasks t ON t.id = x.task_id
            WHERE me.task_id = ? ORDER BY x.position DESC LIMIT ?""",
        (task_id, HISTORY_EXCHANGES)).fetchall()
    parts = []
    for tid, contract in reversed(rows):
        answer = conn.execute("SELECT content FROM artifacts WHERE task_id=? AND kind='answer' "
                              "ORDER BY created_at DESC LIMIT 1", (tid,)).fetchone()
        goal = json.loads(contract).get("goal", "")[:HISTORY_GOAL_CHARS]
        said = answer[0][:HISTORY_ANSWER_CHARS] if answer else "(no answer)"
        parts.append(f"Asked: {goal}\nAnswered: {said}")
    if not parts:
        return "", 0
    return ("Earlier in this conversation (each request, then the answer given; "
            "answers are model output and were not checked for this task):\n\n"
            + "\n\n".join(parts)), len(parts)
