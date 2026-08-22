"""M1.1 — schema and connection. Spec section 1.1, plus the tables M12/M13 need.

Rule that outranks convenience: nothing UPDATEs or DELETEs from `events`.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

# Serializes the chain-critical read-then-write sections: appending an event
# reads the current head to compute prev_hash, and execute_once checks for a
# receipt before running a side effect. Two threads interleaving either one
# corrupts the guarantee — a forked chain, or an effect performed twice.
#
# A single process-wide lock is deliberate. The control plane serves requests
# from a threadpool, so "one connection, one thread" is not achievable; what
# IS achievable is one writer at a time, which is all the chain needs.
_CHAIN_LOCK = threading.RLock()


def chain_lock() -> threading.RLock:
    return _CHAIN_LOCK

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Task graph. Tasks form a tree via parent_id.
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    parent_id       TEXT REFERENCES tasks(id),
    contract_json   TEXT NOT NULL,
    status          TEXT NOT NULL,     -- pending|running|paused|done|failed|halted
    budget_json     TEXT NOT NULL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

-- Append-only event log. THE hash chain lives here.
-- Never UPDATE or DELETE from this table. Ever.
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

-- Content stored by reference. The ledger's backing store.
CREATE TABLE IF NOT EXISTS artifacts (
    id           TEXT PRIMARY KEY,      -- content hash
    task_id      TEXT NOT NULL REFERENCES tasks(id),
    kind         TEXT NOT NULL,         -- tool_output|plan|answer|doc|skill_draft
    content      TEXT NOT NULL,
    taint        TEXT NOT NULL,         -- trusted|user|untrusted
    origin_json  TEXT NOT NULL,
    n_tokens     INTEGER NOT NULL,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);

-- Idempotency for side-effecting tools.
CREATE TABLE IF NOT EXISTS receipts (
    idempotency_key TEXT PRIMARY KEY,
    tool_name       TEXT NOT NULL,
    args_hash       TEXT NOT NULL,
    result_json     TEXT NOT NULL,
    created_at      REAL NOT NULL
);

-- Semantic cache: operation results, not tokens.
CREATE TABLE IF NOT EXISTS semantic_cache (
    key         TEXT PRIMARY KEY,       -- hash(op, normalized_input)
    value_json  TEXT NOT NULL,
    taint       TEXT NOT NULL,
    source_hash TEXT,
    created_at  REAL NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0
);

-- Skills. status drives the lifecycle.
CREATE TABLE IF NOT EXISTS skills (
    id            TEXT PRIMARY KEY,     -- content hash
    name          TEXT NOT NULL,
    version       INTEGER NOT NULL,
    body          TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    status        TEXT NOT NULL,        -- candidate|shadow|promoted|demoted|rejected
    created_at    REAL NOT NULL,
    UNIQUE(name, version)
);

-- Every paired A/B trial. This table is the research result.
CREATE TABLE IF NOT EXISTS skill_trials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id    TEXT NOT NULL REFERENCES skills(id),
    task_key    TEXT NOT NULL,          -- identifies the paired task
    arm         TEXT NOT NULL,          -- skill|control
    success     INTEGER NOT NULL,       -- 0 or 1
    score       REAL,                   -- optional continuous score
    paid_tokens INTEGER NOT NULL,
    free_tokens INTEGER NOT NULL,
    wall_ms     INTEGER NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trials_skill ON skill_trials(skill_id, task_key);

-- Skill invocations, for M9.5 decay.
CREATE TABLE IF NOT EXISTS skill_invocations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id   TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    success    INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_invocations_skill ON skill_invocations(skill_id);

-- Per-call telemetry. One row per model call.
CREATE TABLE IF NOT EXISTS calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL,
    backend         TEXT NOT NULL,
    purpose         TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL,
    wall_ms         INTEGER NOT NULL,
    escalated       INTEGER NOT NULL DEFAULT 0,
    thinking        TEXT NOT NULL DEFAULT 'unknown',
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_task ON calls(task_id);

-- Monitor findings. SEPARATE. Never joined into agent context. See M8.3.
CREATE TABLE IF NOT EXISTS monitor_flags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    severity    TEXT NOT NULL,
    finding     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

-- M12: human approvals. Parked tasks live here; no process spins.
CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    step_id     TEXT,
    tool        TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    preview     TEXT NOT NULL,
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL,          -- pending|approved|denied
    decided_at  REAL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

-- M13.3: auto-review decisions. Visible to the agent, unlike monitor_flags.
-- Two tables, two read paths, no joins between them.
CREATE TABLE IF NOT EXISTS review_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    step_id     TEXT,
    tool        TEXT NOT NULL,
    arg_taint   TEXT NOT NULL,
    verdict     TEXT NOT NULL,          -- approve|deny|escalate
    rationale   TEXT NOT NULL,
    policy_rule TEXT,
    backend     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_task ON review_decisions(task_id);

-- M13.1: plans. The approved hash is pinned and checked before every step.
CREATE TABLE IF NOT EXISTS plans (
    id            TEXT PRIMARY KEY,     -- content hash
    task_id       TEXT NOT NULL REFERENCES tasks(id),
    body_json     TEXT NOT NULL,
    status        TEXT NOT NULL,        -- drafted|approved|rejected|superseded
    approved_hash TEXT,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_task ON plans(task_id);

-- ---------------------------------------------------------------------
-- USER-AUTHORED CONTENT
--
-- Everything a person writes lives here rather than in files they hand-edit:
-- eval suites, the reviewer's policy, grounding notes. Files are still
-- importable, but they are an import path, not the source of truth.
--
-- `owner` is on every table from the start and defaults to 'local'. This is
-- single-user today; adding the column later would mean migrating live rows,
-- and it costs nothing now.
-- ---------------------------------------------------------------------

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
    -- A regression here vetoes promotion outright (M9.2).
    catastrophic   INTEGER NOT NULL DEFAULT 0,
    position       INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    UNIQUE(suite_id, task_key)
);
CREATE INDEX IF NOT EXISTS idx_suite_tasks ON suite_tasks(suite_id, position);

-- Policy text and grounding notes. `kind` keeps them in one table because
-- they have identical lifecycles: authored, versioned by edit, activated.
CREATE TABLE IF NOT EXISTS documents (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,          -- policy | grounding
    name       TEXT NOT NULL,
    owner      TEXT NOT NULL DEFAULT 'local',
    body       TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(owner, kind, name)
);
CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind, active);

-- Every prior version of an authored document, kept forever.
--
-- The reviewer policy decides what an unattended agent may do. A security
-- control that can be silently overwritten is not much of a control: you
-- need to be able to answer "what did it say when that run happened, and who
-- changed it since". Append-only, like the event log and for the same reason.
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

-- M13.4: deviations, so a high deviation rate is measurable.
CREATE TABLE IF NOT EXISTS deviations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    step_id    TEXT NOT NULL,
    drift      REAL NOT NULL,
    action     TEXT NOT NULL,
    discovery  TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False because the control plane (M12) serves requests
    # on a threadpool; without it every endpoint raises ProgrammingError. The
    # safety this drops is replaced by chain_lock() around the critical
    # sections, plus SQLite's own locking for everything else.
    conn = sqlite3.connect(
        str(path), timeout=30.0, isolation_level=None, check_same_thread=False
    )
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = FULL")  # write-before-act needs real fsyncs
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def open_db(db_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    init_schema(conn)
    return conn
