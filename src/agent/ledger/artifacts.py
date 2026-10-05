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
    aid = sha256_hex(content)[:32]
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
    conn: sqlite3.Connection, aid: str, mode: str = "summary", max_tokens: int = 400
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
    lines = content.splitlines()
    head_n = max(6, int(len(lines) * 0.25))
    tail_n = max(4, int(len(lines) * 0.15))
    if head_n + tail_n >= len(lines):
        return content
    head = "\n".join(lines[:head_n])
    tail = "\n".join(lines[-tail_n:])
    dropped = len(lines) - head_n - tail_n
    return (
        f"{head}\n"
        f"[... {dropped} lines elided · full text in artifact {aid} · "
        f'expand_artifact("{aid}") ...]\n'
        f"{tail}"
    )
