"""B4 — full-text search over prior sessions.

The index itself lives in `db.py`'s schema, maintained by triggers. This
module is the read path, the rebuild path, and the taint rule.

THREE THINGS THAT ARE NOT NEGOTIABLE HERE:

**Results are UNTRUSTED, as a floor rather than a computation.** An `answer`
artifact carries `taint=trusted` in the artifacts table, and it would be easy
to propagate that. It would also be wrong: an answer is trusted as the output
of a verified run, not as input to a *later* one, and its text may quote a web
page the agent fetched three steps earlier. Search re-injects old content into
a fresh context, which is the same shape as reading a file. A computed join
would return TRUSTED for exactly the rows most worth attacking, so the taint
is a constant.

**The index is derived. The log wins.** Nothing here writes to `events`, and
`rebuild()` can drop and repopulate the whole table without touching the hash
chain. If the two ever disagree, the fix is to rebuild the index, never to
reconcile the log toward it.

**Results are bounded.** Fifty matches pasted into context defeats the whole
ledger discipline this project is built on - the point of storing content by
reference is that the agent does not carry it all at once. A search returning
more text than the compaction saves is a regression wearing a feature's hat.

WHAT "SESSION" MEANS HERE. There is no sessions table yet; Phase 9 introduces
one with parent/child lineage. Today a session is the ROOT of a task tree -
`tasks.parent_id` walked to the top - so a delegated subtask reports the same
session as its parent. When real session rows arrive this is the column that
gets repointed, and callers do not change.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from .types import Taint

# Bounds. A search is a lookup, not a bulk export.
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
SNIPPET_TOKENS = 24          # words of context around the hit
MAX_BODY_CHARS = 400         # hard cap per result, after snippeting

# Kinds the triggers index. Named here as well so `rebuild()` and the schema
# cannot drift apart silently - `verify()` checks they agree.
INDEXED_ARTIFACT_KINDS = ("answer", "plan", "doc", "skill_draft")


class SearchUnavailable(RuntimeError):
    """FTS5 is missing from this SQLite build."""


@dataclass
class Hit:
    session_id: str
    task_id: str
    source: str
    kind: str
    ref: str
    created_at: float
    snippet: str
    # Always UNTRUSTED. A field rather than a bare constant so a caller that
    # forwards the whole Hit cannot lose it on the way.
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
    """Root of the task tree. See the module docstring on what a session is."""
    row = conn.execute(
        """WITH RECURSIVE up(id, parent_id) AS (
               SELECT id, parent_id FROM tasks WHERE id = ?
               UNION ALL
               SELECT t.id, t.parent_id FROM tasks t JOIN up ON t.id = up.parent_id
           )
           SELECT id FROM up WHERE parent_id IS NULL LIMIT 1""",
        (task_id,),
    ).fetchone()
    return row[0] if row else task_id


def search(conn: sqlite3.Connection, query: str, limit: int = DEFAULT_LIMIT,
           offset: int = 0) -> list[Hit]:
    """Full-text search. Raises ValueError on a malformed query.

    FTS5 has its own query grammar and a stray quote or a bare `AND` raises
    OperationalError from deep inside sqlite. That must not reach the agent
    loop as a crash: a bad search is a bad tool argument, which the loop
    already knows how to hand back to the model.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    if not (query or "").strip():
        return []
    try:
        rows = conn.execute(
            f"""SELECT snippet(search_index, 0, '', '', ' … ', {SNIPPET_TOKENS}),
                       source, ref, task_id, kind, created_at
                FROM search_index
                WHERE search_index MATCH ?
                ORDER BY rank
                LIMIT ? OFFSET ?""",
            (query, limit, offset),
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
    """Drop the index and rebuild it from tasks and artifacts.

    THE LOG WINS. This is what that sentence means operationally: the source
    tables are authoritative and the index is disposable, so recovering from
    any disagreement is a rebuild rather than an investigation.

    Deterministic given the same source rows, which is what makes
    `test_a_rebuild_reproduces_the_index_exactly` a real check rather than a
    tautology - it compares against an index built incrementally by triggers
    over the same data.
    """
    with conn:
        conn.execute("DELETE FROM search_index")
        conn.execute(
            """INSERT INTO search_index(body, source, ref, task_id, kind, created_at)
               SELECT contract_json, 'contract', id, id, 'contract', created_at
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
    """Compare the index against its sources without changing either.

    Reports rather than repairs. A count mismatch means a write path bypassed
    the triggers, which is a defect to find, not a number to quietly correct.
    """
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
    """Contracts are stored as JSON; a raw dump is unreadable as a snippet."""
    try:
        d = json.loads(body)
    except (ValueError, TypeError):
        return body[:MAX_BODY_CHARS]
    goal = str(d.get("goal") or "")
    crit = str(d.get("success_criterion") or "")
    return (f"{goal} [criterion: {crit}]" if crit else goal)[:MAX_BODY_CHARS]
