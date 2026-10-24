from __future__ import annotations

import json
import sqlite3
import time

from ..kernel.events import canonical, sha256_hex
from ..kernel.types import Taint
from ..tokens import count_tokens


def write_artifact(
    conn: sqlite3.Connection,
    task_id: str,
    kind: str,
    content: str,
    taint: Taint,
    origin: dict,
) -> str:
    # per task, kind and taint: one row per id, so a shared id hid the second task's row, or kept the first taint
    aid = sha256_hex(canonical([task_id, kind, taint.value, content]))[:32]
    conn.execute(
        """INSERT OR IGNORE INTO artifacts(id, task_id, kind, content, taint,
                                           origin_json, n_tokens, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            aid,
            task_id,
            kind,
            content,
            taint.value,
            canonical(origin),
            count_tokens(content),
            time.time(),
        ),
    )
    conn.commit()
    return aid


def read_artifact(conn: sqlite3.Connection, aid: str) -> dict | None:
    row = conn.execute(
        """SELECT id, task_id, kind, content, taint, origin_json, n_tokens
           FROM artifacts WHERE id=?""",
        (aid,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "task_id": row[1],
        "kind": row[2],
        "content": row[3],
        "taint": Taint(row[4]),
        "origin": json.loads(row[5]),
        "n_tokens": row[6],
    }


def project(
    conn: sqlite3.Connection, aid: str, mode: str = "summary", max_tokens: int = 400, hint: str = ""
) -> str:
    art = read_artifact(conn, aid)
    if art is None:
        return f"[artifact {aid} · missing]"

    if mode == "ref":
        return (
            f"[artifact {aid} · {art['kind']} · {art['n_tokens']:,} tokens · "
            f'taint={art["taint"].value} · expand_artifact("{aid}")]'
        )

    if mode == "full":
        return art["content"]

    content: str = art["content"]
    if art["n_tokens"] <= max_tokens:
        return content
    cpt = max(1.0, len(content) / max(1, art["n_tokens"]))
    hb, tb = int(max_tokens * 0.6 * cpt), int(max_tokens * 0.4 * cpt)
    if hb + tb >= len(content):
        return content
    head = content[:hb]
    cut = head.rfind("\n")
    if cut > hb * 0.7:
        head = head[:cut]
    tail = content[-tb:]
    cut = tail.find("\n")
    if 0 <= cut < tb * 0.3:
        tail = tail[cut + 1:]
    dropped = len(content) - len(head) - len(tail)
    return (
        f"{head}\n"
        f"[... {dropped:,} characters elided · full text in artifact {aid} · "
        f'expand_artifact("{aid}"){hint} ...]\n'
        f"{tail}"
    )
