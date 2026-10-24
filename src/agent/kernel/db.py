from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

_CHAIN_LOCK = threading.RLock()


def chain_lock(conn: sqlite3.Connection | None = None) -> threading.RLock:
    return _CHAIN_LOCK

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    parent_id       TEXT REFERENCES tasks(id),
    contract_json   TEXT NOT NULL,
    status          TEXT NOT NULL,
    budget_json     TEXT NOT NULL,
    origin          TEXT NOT NULL DEFAULT 'local',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    seq         INTEGER NOT NULL,
    type        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE(task_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(task_id, type);

CREATE TABLE IF NOT EXISTS artifacts (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL REFERENCES tasks(id),
    kind         TEXT NOT NULL,
    content      TEXT NOT NULL,
    taint        TEXT NOT NULL,
    origin_json  TEXT NOT NULL,
    n_tokens     INTEGER NOT NULL,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);

CREATE TABLE IF NOT EXISTS receipts (
    idempotency_key TEXT PRIMARY KEY,
    tool_name       TEXT NOT NULL,
    args_hash       TEXT NOT NULL,
    result_json     TEXT NOT NULL,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS semantic_cache (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    taint       TEXT NOT NULL,
    source_hash TEXT,
    created_at  REAL NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS skills (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    version       INTEGER NOT NULL,
    body          TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    status        TEXT NOT NULL,
    created_at    REAL NOT NULL,
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS skill_trials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id    TEXT NOT NULL REFERENCES skills(id),
    task_key    TEXT NOT NULL,
    arm         TEXT NOT NULL,
    success     INTEGER NOT NULL,
    score       REAL,
    paid_tokens INTEGER NOT NULL,
    free_tokens INTEGER NOT NULL,
    wall_ms     INTEGER NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trials_skill ON skill_trials(skill_id, task_key);

CREATE TABLE IF NOT EXISTS skill_invocations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id   TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    success    INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_invocations_skill ON skill_invocations(skill_id);

CREATE TABLE IF NOT EXISTS calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL,
    backend         TEXT NOT NULL,
    model           TEXT,
    purpose         TEXT NOT NULL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cache_read_tokens  INTEGER,
    cache_write_tokens INTEGER,
    cost_usd        REAL,
    wall_ms         INTEGER,
    escalated       INTEGER NOT NULL DEFAULT 0,
    thinking        TEXT NOT NULL DEFAULT 'unknown',
    created_at      REAL NOT NULL,
    est_input_tokens INTEGER,
    outcome         TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_task ON calls(task_id);
CREATE INDEX IF NOT EXISTS idx_calls_route ON calls(backend, purpose, output_tokens);

CREATE TABLE IF NOT EXISTS monitor_flags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    severity    TEXT NOT NULL,
    finding     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    step_id     TEXT,
    tool        TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    preview     TEXT NOT NULL,
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL,
    decided_at  REAL,
    created_at  REAL NOT NULL,
    consumed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

CREATE TABLE IF NOT EXISTS review_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    step_id     TEXT,
    tool        TEXT NOT NULL,
    arg_taint   TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    rationale   TEXT NOT NULL,
    policy_rule TEXT,
    backend     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_task ON review_decisions(task_id);

CREATE TABLE IF NOT EXISTS plans (
    id            TEXT NOT NULL,
    task_id       TEXT NOT NULL REFERENCES tasks(id),
    body_json     TEXT NOT NULL,
    status        TEXT NOT NULL,
    approved_hash TEXT,
    created_at    REAL NOT NULL,
    PRIMARY KEY (id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_plans_task ON plans(task_id);


CREATE TABLE IF NOT EXISTS suites (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    owner       TEXT NOT NULL DEFAULT 'local',
    description TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE(owner, name)
);

CREATE TABLE IF NOT EXISTS suite_tasks (
    id             TEXT PRIMARY KEY,
    suite_id       TEXT NOT NULL REFERENCES suites(id) ON DELETE CASCADE,
    task_key       TEXT NOT NULL,
    goal           TEXT NOT NULL,
    success_criterion TEXT NOT NULL DEFAULT 'expected_value',
    output_schema_json TEXT NOT NULL DEFAULT '{}',
    artifact_scope_json TEXT NOT NULL DEFAULT '["work"]',
    fixtures_json  TEXT NOT NULL DEFAULT '{}',
    budget_usd     REAL,
    budget_seconds INTEGER,
    catastrophic   INTEGER NOT NULL DEFAULT 0,
    position       INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    UNIQUE(suite_id, task_key)
);
CREATE INDEX IF NOT EXISTS idx_suite_tasks ON suite_tasks(suite_id, position);

CREATE TABLE IF NOT EXISTS documents (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    owner      TEXT NOT NULL DEFAULT 'local',
    body       TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(owner, kind, name)
);
CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind, active);

CREATE TABLE IF NOT EXISTS document_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    owner       TEXT NOT NULL DEFAULT 'local',
    body        TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'local',
    replaced_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dochist ON document_history(kind, name, replaced_at);

CREATE TABLE IF NOT EXISTS deviations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    step_id    TEXT NOT NULL,
    drift      REAL NOT NULL,
    action     TEXT NOT NULL,
    discovery  TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    body,
    source     UNINDEXED,      -- 'contract' | 'artifact'
    ref        UNINDEXED,
    task_id    UNINDEXED,
    kind       UNINDEXED,
    created_at UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS trg_search_task_ai_v2 AFTER INSERT ON tasks
BEGIN
    INSERT INTO search_index(body, source, ref, task_id, kind, created_at)
    VALUES (coalesce(json_extract(new.contract_json, '$.goal'), '') || ' ' ||
            coalesce(json_extract(new.contract_json, '$.success_criterion'), ''),
            'contract', new.id, new.id, 'contract', new.created_at);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_artifact_ai AFTER INSERT ON artifacts
WHEN new.kind IN ('answer', 'plan', 'doc', 'skill_draft')
BEGIN
    INSERT INTO search_index(body, source, ref, task_id, kind, created_at)
    VALUES (new.content, 'artifact', new.id, new.task_id, new.kind,
            new.created_at);
END;

CREATE TABLE IF NOT EXISTS channel_drops (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT NOT NULL,
    sender      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    body_len    INTEGER NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drops_created ON channel_drops(created_at);

CREATE TABLE IF NOT EXISTS channel_routes (
    task_id     TEXT PRIMARY KEY REFERENCES tasks(id),
    channel     TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_updates (
    channel     TEXT NOT NULL,
    update_id   INTEGER NOT NULL,
    seen_at     REAL NOT NULL,
    PRIMARY KEY (channel, update_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    parent_id       TEXT REFERENCES sessions(id),
    status          TEXT NOT NULL,
    opened_reason   TEXT NOT NULL,
    seed_artifact   TEXT,
    memory_json     TEXT NOT NULL,
    created_at      REAL NOT NULL,
    closed_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_sessions_task ON sessions(task_id);

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0,
    pinned      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS conversation_tasks (
    task_id         TEXT PRIMARY KEY REFERENCES tasks(id),
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    position        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_tasks
    ON conversation_tasks(conversation_id, position);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_one_open
    ON sessions(task_id) WHERE status = 'open';
CREATE TRIGGER IF NOT EXISTS sessions_same_attempt
BEFORE INSERT ON sessions
WHEN NEW.parent_id IS NOT NULL
 AND (SELECT task_id FROM sessions WHERE id = NEW.parent_id) IS NOT NEW.task_id
BEGIN
    SELECT RAISE(ABORT, 'a session may only descend from a session of the same task');
END;

CREATE TABLE IF NOT EXISTS schedules (
    id            TEXT PRIMARY KEY,
    spec          TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    budget_json   TEXT NOT NULL,
    created_at    REAL NOT NULL,
    removed_at    REAL
);
CREATE TABLE IF NOT EXISTS schedule_firings (
    schedule_id   TEXT NOT NULL REFERENCES schedules(id),
    occurrence    INTEGER NOT NULL,     -- the slot's epoch, not when it ran
    outcome       TEXT NOT NULL,
    task_id       TEXT REFERENCES tasks(id),
    detail_json   TEXT NOT NULL,
    recorded_at   REAL NOT NULL,
    PRIMARY KEY (schedule_id, occurrence)
);
CREATE TABLE IF NOT EXISTS notifications (
    id            TEXT PRIMARY KEY,
    task_id       TEXT REFERENCES tasks(id),
    kind          TEXT NOT NULL,
    detail_json   TEXT NOT NULL,
    created_at    REAL NOT NULL,
    seen_at       REAL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path), timeout=30.0, isolation_level=None, check_same_thread=False,
        cached_statements=0,
    )
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


_ADDED_COLUMNS = (
    ("tasks", "origin", "TEXT NOT NULL DEFAULT 'local'"),
    ("approvals", "consumed_at", "REAL"),
    ("calls", "model", "TEXT"),
    ("calls", "est_input_tokens", "INTEGER"),
    ("conversations", "pinned", "INTEGER NOT NULL DEFAULT 0"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> list[str]:
    added = []
    for table, column, decl in _ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            added.append(f"{table}.{column}")
    return added


def _calls_allow_unreturned(conn: sqlite3.Connection) -> bool:
    info = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(calls)")}
    if not info or info.get("input_tokens") == 0:
        return False
    ddl = re.search(r"CREATE TABLE IF NOT EXISTS calls \(.*?\n\);", SCHEMA_SQL, re.S).group(0)
    cols = ("id, task_id, backend, purpose, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, cost_usd, wall_ms, "
            "escalated, thinking, created_at")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("ALTER TABLE calls RENAME TO calls_pre_d36")
        conn.execute(ddl)
        conn.execute(f"INSERT INTO calls({cols}, outcome) "
                     f"SELECT {cols}, 'returned' FROM calls_pre_d36")
        conn.execute("DROP TABLE calls_pre_d36")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_task ON calls(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_route ON calls(backend, purpose, output_tokens)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return True


def _plans_keyed_by_task(conn: sqlite3.Connection) -> bool:
    info = {r[1]: r[5] for r in conn.execute("PRAGMA table_info(plans)")}
    if not info or info.get("task_id"):
        return False
    ddl = re.search(r"CREATE TABLE IF NOT EXISTS plans \(.*?\n\);",
                    SCHEMA_SQL, re.S).group(0)
    cols = "id, task_id, body_json, status, approved_hash, created_at"
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("ALTER TABLE plans RENAME TO plans_pre_d24")
        conn.execute(ddl)
        conn.execute(f"INSERT INTO plans({cols}) SELECT {cols} FROM plans_pre_d24")
        conn.execute("DROP TABLE plans_pre_d24")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_task ON plans(task_id)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
    return True


def _search_indexes_no_grader(conn: sqlite3.Connection) -> bool:
    old = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' "
        "AND name='trg_search_task_ai'").fetchone()
    if not old:
        return False
    from .search import rebuild

    conn.execute("DROP TRIGGER trg_search_task_ai")
    conn.commit()
    rebuild(conn)
    return True


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _add_missing_columns(conn)
    _calls_allow_unreturned(conn)
    _plans_keyed_by_task(conn)
    _search_indexes_no_grader(conn)
    conn.commit()


def open_db(db_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    init_schema(conn)
    return conn


def snapshot(conn: sqlite3.Connection, dest: str | Path) -> Path:
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"{dest} exists; refusing to overwrite evidence")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(str(dest))
    try:
        conn.backup(out)
    finally:
        out.close()
    return dest
