from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from .types import Taint

DEFAULT_LIMIT = 5
MAX_LIMIT = 20
SNIPPET_TOKENS = 24
MAX_BODY_CHARS = 400

INDEXED_ARTIFACT_KINDS = ("answer", "plan", "doc", "skill_draft")

CONTRACT_INDEX_BODY = (
    "coalesce(json_extract(contract_json, '$.goal'), '') || ' ' || "
    "coalesce(json_extract(contract_json, '$.success_criterion'), '')")


class SearchUnavailable(RuntimeError):
    ...


@dataclass
class Hit:
    session_id: str
    task_id: str
    source: str
    kind: str
    ref: str
    created_at: float
    snippet: str
    taint: Taint = field(default=Taint.UNTRUSTED)

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "source": self.source,
            "kind": self.kind,
            "ref": self.ref,
            "created_at": self.created_at,
            "snippet": self.snippet,
            "taint": self.taint.value,
        }


def _session_of(conn: sqlite3.Connection, task_id: str) -> str:
    from .sessions import root_session

    row = conn.execute(
        """WITH RECURSIVE up(id, parent_id) AS (
               SELECT id, parent_id FROM tasks WHERE id = ?
               UNION ALL
               SELECT t.id, t.parent_id FROM tasks t JOIN up ON t.id = up.parent_id
           )
           SELECT id FROM up WHERE parent_id IS NULL LIMIT 1""",
        (task_id,),
    ).fetchone()
    root = row[0] if row else task_id
    return root_session(conn, root) or root


def search(conn: sqlite3.Connection, query: str, limit: int = DEFAULT_LIMIT,
           offset: int = 0,
           allowed_tasks: set[str] | None = None) -> list[Hit]:
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    if not (query or "").strip():
        return []
    if allowed_tasks is not None and not allowed_tasks:
        return []
    scope_sql, scope_args = "", []
    if allowed_tasks is not None:
        scope_sql = " AND task_id IN (%s)" % ",".join("?" * len(allowed_tasks))
        scope_args = sorted(allowed_tasks)
    try:
        rows = conn.execute(
            f"""SELECT snippet(search_index, 0, '', '', ' … ', {SNIPPET_TOKENS}),
                       source, ref, task_id, kind, created_at
                FROM search_index
                WHERE search_index MATCH ?{scope_sql}
                ORDER BY rank
                LIMIT ? OFFSET ?""",
            (query, *scope_args, limit, offset),
        ).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            raise SearchUnavailable(
                "search_index is missing; this SQLite build may lack FTS5"
            ) from e
        raise ValueError(f"malformed search query {query!r}: {e}") from e

    return [
        Hit(session_id=_session_of(conn, task_id), task_id=task_id,
            source=source, kind=kind, ref=ref, created_at=created_at,
            snippet=(snip or "")[:MAX_BODY_CHARS])
        for snip, source, ref, task_id, kind, created_at in rows
    ]


def rebuild(conn: sqlite3.Connection) -> int:
    with conn:
        conn.execute("DELETE FROM search_index")
        conn.execute(
            f"""INSERT INTO search_index(body, source, ref, task_id, kind, created_at)
               SELECT {CONTRACT_INDEX_BODY}, 'contract', id, id, 'contract',
                      created_at
               FROM tasks ORDER BY created_at, id"""
        )
        placeholders = ",".join("?" * len(INDEXED_ARTIFACT_KINDS))
        conn.execute(
            f"""INSERT INTO search_index(body, source, ref, task_id, kind, created_at)
                SELECT content, 'artifact', id, task_id, kind, created_at
                FROM artifacts WHERE kind IN ({placeholders})
                ORDER BY created_at, id""",
            INDEXED_ARTIFACT_KINDS,
        )
    return conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]


def verify(conn: sqlite3.Connection) -> dict:
    indexed = conn.execute(
        "SELECT COUNT(*) FROM search_index").fetchone()[0]
    contracts = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    placeholders = ",".join("?" * len(INDEXED_ARTIFACT_KINDS))
    arts = conn.execute(
        f"SELECT COUNT(*) FROM artifacts WHERE kind IN ({placeholders})",
        INDEXED_ARTIFACT_KINDS,
    ).fetchone()[0]
    expected = contracts + arts
    return {
        "indexed": indexed,
        "expected": expected,
        "contracts": contracts,
        "artifacts": arts,
        "consistent": indexed == expected,
        "excluded_tool_outputs": conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE kind = 'tool_output'"
        ).fetchone()[0],
    }


def summarise_contract(body: str) -> str:
    try:
        d = json.loads(body)
    except (ValueError, TypeError):
        return body[:MAX_BODY_CHARS]
    goal = str(d.get("goal") or "")
    crit = str(d.get("success_criterion") or "")
    return (f"{goal} [criterion: {crit}]" if crit else goal)[:MAX_BODY_CHARS]
