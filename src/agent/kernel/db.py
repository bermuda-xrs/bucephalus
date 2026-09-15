"""M1.1 — schema and connection. Spec section 1.1, plus the tables M12/M13 need.

Rule that outranks convenience: nothing UPDATEs or DELETEs from `events`.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

# Serializes the chain-critical read-then-write sections: appending an event
# reads the current head to compute prev_hash, and execute_once checks for a
# receipt before running a side effect. Two writers interleaving either one
# corrupts the guarantee — a forked chain, or an effect performed twice.
#
# Two layers, because threads and processes are different problems:
#
#   * an RLock serializes threads inside this process — the control plane
#     serves from a threadpool, so this is the case that actually occurs, and
#     being reentrant lets append() nest inside execute_once() safely.
#
# ONE PROCESS ONLY. This lock does not cross process boundaries, so a second
# uvicorn worker, or a CLI run against the same database while the server is
# up, can still fork the chain.
#
# Doing better needs more than a longer transaction. `BEGIN IMMEDIATE` around
# execute_once would hold SQLite's write lock across an arbitrary side effect
# — a 30-second tool call would stall every other writer — so the correct
# shape is claim-then-execute: insert the receipt row first and let the
# PRIMARY KEY on idempotency_key reject the loser of a race, then fill in the
# result. That is a real change to the receipt protocol and is deliberately
# not smuggled in here; the single-worker constraint is documented instead.
_CHAIN_LOCK = threading.RLock()


def chain_lock(conn: sqlite3.Connection | None = None) -> threading.RLock:
    """`conn` is accepted for call-site symmetry and currently unused."""
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
    -- A4. Where this task came from: 'local' or a channel name. DURABLE, and
    -- never updated. Restrictions are derived from it at check time rather
    -- than applied at creation, so a parked channel task that outlives the
    -- process it was created in comes back restricted. See security/origin.py.
    origin          TEXT NOT NULL DEFAULT 'local',
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
    -- D36: written when the call STARTS, so usage is NULL until it returns.
    -- A row whose usage never arrives is the record of a call that never
    -- returned - the stalled, the crashed, the killed - which the old
    -- write-on-return shape left out of the table entirely.
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cache_read_tokens  INTEGER,
    cache_write_tokens INTEGER,
    cost_usd        REAL,
    wall_ms         INTEGER,            -- set on return, or on an abort
    escalated       INTEGER NOT NULL DEFAULT 0,
    thinking        TEXT NOT NULL DEFAULT 'unknown',
    created_at      REAL NOT NULL,      -- the START (pre-D36 rows: the return)
    outcome         TEXT                -- NULL in flight or never ended;
                                        -- returned | stalled | error | interrupted
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
    created_at  REAL NOT NULL,
    -- When the resumed task ACTED on the decision. A decision nobody reads is
    -- a decision with no effect, which is what every approval was until
    -- 2026-09-13: see execute/loop.py `_release_decided`.
    consumed_at REAL
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

-- ---------------------------------------------------------------------
-- B4: full-text search over prior work. DERIVED DATA.
--
-- Nothing here is part of the hash chain, and nothing here may be. The
-- chain covers `events`; this table is built from `tasks` and `artifacts`
-- and can be dropped and rebuilt at any time without affecting
-- verification. IF THE INDEX AND THE LOG DISAGREE, THE LOG WINS - see
-- kernel/search.py:rebuild().
--
-- WHAT IS INDEXED, and why not everything:
--   * task contracts   - the goal and success criterion, which is what a
--                        person actually typed
--   * answer/plan/doc  - what the run produced
--
-- NOT tool_output. It is 76% of artifact rows in the live database (347 of
-- 460), it is machine-generated directory listings and program output, and
-- indexing it would mean every search returns tool noise ahead of the two
-- sentences someone wrote. High volume, low signal.
--
-- Maintained by TRIGGERS rather than by call sites. Two Python functions
-- write artifacts today, but a third would silently stop indexing, and a
-- derived table that is quietly incomplete is worse than none: a search that
-- finds nothing looks identical to a session that never happened.
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    body,
    source     UNINDEXED,      -- 'contract' | 'artifact'
    ref        UNINDEXED,      -- task id, or artifact content hash
    task_id    UNINDEXED,
    kind       UNINDEXED,
    created_at UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS trg_search_task_ai AFTER INSERT ON tasks
BEGIN
    INSERT INTO search_index(body, source, ref, task_id, kind, created_at)
    VALUES (new.contract_json, 'contract', new.id, new.id, 'contract',
            new.created_at);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_artifact_ai AFTER INSERT ON artifacts
WHEN new.kind IN ('answer', 'plan', 'doc', 'skill_draft')
BEGIN
    INSERT INTO search_index(body, source, ref, task_id, kind, created_at)
    VALUES (new.content, 'artifact', new.id, new.task_id, new.kind,
            new.created_at);
END;

-- A4. Messages from senders who are not on the allowlist. DROPPED, not
-- queued: no task row, nothing a human can later approve by mistake.
--
-- A separate table rather than an event, for two reasons that both matter.
-- `events.task_id` is NOT NULL and references a task, and a drop by
-- definition has no task; inventing one to hold the record would create
-- exactly the queued object this is supposed to avoid. And the events table
-- feeds `search_index`, so a drop recorded there would be retrievable later
-- by `search_history` — an unauthenticated stranger writing into the corpus
-- the agent reads is the contamination vector Phase 2 spent its time closing.
--
-- The BODY IS NOT STORED. A hash and a length are enough to recognise a
-- repeat or prove what arrived; the text itself is attacker-controlled and
-- keeping it buys nothing that a hash does not.
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

-- A4. Where to send a task's approval request back to. ROUTING, not a claim
-- about what happened, so it is a plain table and not an event.
--
-- Durable for the same reason `tasks.origin` is. A channel task parks far
-- more often than a local one, and the whole point of parking is that it
-- costs nothing to wait — so the wait routinely outlives the process. Held in
-- memory, this fails closed rather than open (the approval stays pending),
-- but it fails: the human is never asked, and a task nobody was asked about
-- waits forever.
CREATE TABLE IF NOT EXISTS channel_routes (
    task_id     TEXT PRIMARY KEY REFERENCES tasks(id),
    channel     TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

-- A4. Which inbound updates have been claimed. Telegram confirms an update
-- only when the NEXT getUpdates asks for a higher offset, so a process that
-- dies after handling a message and before polling again is sent the same
-- message on restart. With the offset held in memory that was a second task
-- from one message. Bounded like channel_drops: strangers' updates land here.
CREATE TABLE IF NOT EXISTS channel_updates (
    channel     TEXT NOT NULL,
    update_id   INTEGER NOT NULL,
    seen_at     REAL NOT NULL,
    PRIMARY KEY (channel, update_id)
);

-- B1. One context-window lifetime of one task attempt. A boundary closes a
-- session and opens a child: a lineage chain, never a rewritten transcript.
-- See kernel/sessions.py.
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    parent_id       TEXT REFERENCES sessions(id),
    status          TEXT NOT NULL,          -- open | closed
    opened_reason   TEXT NOT NULL,          -- start | boundary
    seed_artifact   TEXT,                   -- what a boundary left the child
    memory_json     TEXT NOT NULL,          -- the tier-1 snapshot of its prefix
    created_at      REAL NOT NULL,
    closed_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_sessions_task ON sessions(task_id);
-- At most one open session per task: a boundary closes before it opens.
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_one_open
    ON sessions(task_id) WHERE status = 'open';
-- 9.7, IN THE SCHEMA rather than in a function someone must remember to call:
-- a session may only descend from a session of the SAME task. What a boundary
-- seeds a child with is derived content, and derived content inherits its
-- attempt's scope. A missing parent is refused by the same rule.
CREATE TRIGGER IF NOT EXISTS sessions_same_attempt
BEFORE INSERT ON sessions
WHEN NEW.parent_id IS NOT NULL
 AND (SELECT task_id FROM sessions WHERE id = NEW.parent_id) IS NOT NEW.task_id
BEGIN
    SELECT RAISE(ABORT, 'a session may only descend from a session of the same task');
END;

-- A3. A schedule is a FACT; its next fire is DERIVED on read and stored
-- nowhere (schedule.py). A removal is a fact too, not a delete.
CREATE TABLE IF NOT EXISTS schedules (
    id            TEXT PRIMARY KEY,
    spec          TEXT NOT NULL,        -- five-field cron, local time
    contract_json TEXT NOT NULL,        -- the task each firing creates
    budget_json   TEXT NOT NULL,
    created_at    REAL NOT NULL,
    removed_at    REAL
);
-- One row per occurrence DEALT WITH: fired, or missed. The primary key is what
-- makes a double fire impossible rather than unlikely - two supervisors, or one
-- restarted mid-wake, cannot both claim an occurrence.
CREATE TABLE IF NOT EXISTS schedule_firings (
    schedule_id   TEXT NOT NULL REFERENCES schedules(id),
    occurrence    INTEGER NOT NULL,     -- the slot's epoch, not when it ran
    outcome       TEXT NOT NULL,        -- fired | missed
    task_id       TEXT REFERENCES tasks(id),
    detail_json   TEXT NOT NULL,
    recorded_at   REAL NOT NULL,
    PRIMARY KEY (schedule_id, occurrence)
);
-- 11.2: a scheduled task that parks raises one of these. The row is the fact
-- a delivery channel reads; the approval it points at is where a human acts.
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


# Columns added after a database may already exist. `CREATE TABLE IF NOT
# EXISTS` does not alter a table that is already there, so a schema change to
# an existing column list is invisible without this — and the failure mode is
# a query that raises on someone else's machine and nowhere in the suite.
#
# ADD COLUMN with a constant default is the one ALTER SQLite does cheaply and
# without rewriting the table. Anything beyond that belongs in a real
# migration, and this list is kept short so that stays true.
_ADDED_COLUMNS = (
    ("tasks", "origin", "TEXT NOT NULL DEFAULT 'local'"),
    ("approvals", "consumed_at", "REAL"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> list[str]:
    added = []
    for table, column, decl in _ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue  # table not created yet; the schema will include it
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            added.append(f"{table}.{column}")
    return added


def _calls_allow_unreturned(conn: sqlite3.Connection) -> bool:
    """D36: rebuild a pre-D36 `calls` so a row can exist before its call returns.

    SQLite cannot drop NOT NULL in place, so the table is renamed, recreated
    from SCHEMA_SQL's definition, refilled with every row unchanged - each
    marked `returned`, which is what every pre-D36 row is by construction -
    and the old one dropped, in one transaction. Idempotent: a table whose
    usage columns already accept NULL is left alone.
    """
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
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return True


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _add_missing_columns(conn)
    _calls_allow_unreturned(conn)
    conn.commit()


def open_db(db_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    init_schema(conn)
    return conn


def snapshot(conn: sqlite3.Connection, dest: str | Path) -> Path:
    """Copy a LIVE database correctly: SQLite's backup API, not a file copy.

    A copy of `agent.db` alone loses every row still in `agent.db-wal` - the
    most recent rows, which are the ones evidence is usually about. This
    project did it twice in ten minutes (C41): once in a test that then could
    not fail, once in a `cp` that read back zero of 283 tasks. Copying all
    three files by hand is racy while a writer is live; the backup API takes a
    consistent snapshot through the same locks every connection uses.

    Refuses to overwrite. Evidence preservation that silently replaces earlier
    evidence is the other way to get this wrong.
    """
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
