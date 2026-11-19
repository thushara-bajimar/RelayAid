"""SQLite storage for one node. Every node owns a separate database file.

Table groups
------------
1. Replicated source of truth : events (append-only, signed)
2. Local node state           : meta, peer_state, sync_sessions, sim_state, audit_log, counters
3. Materialized projections   : teams, needs, need_notes, supplies, allocation_rights,
                                rights_transfers, resource_allocations, rights_requests,
                                shelters, shelter_updates, people, people_evidence, tasks,
                                task_notes, conflicts, rejected_events
   These are rebuilt deterministically from `events` (see projection.py) and are never
   synchronized themselves.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 1. Replicated, append-only event log -------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    origin        TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    lamport       INTEGER NOT NULL,
    vv            TEXT NOT NULL,          -- JSON version vector (causal past incl. self)
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    type          TEXT NOT NULL,
    payload       TEXT NOT NULL,          -- JSON; sensitive fields are AES-GCM ciphertext
    created_at    TEXT NOT NULL,
    schema        INTEGER NOT NULL,
    sig           TEXT NOT NULL,
    received_at   TEXT NOT NULL,
    received_from TEXT NOT NULL,          -- 'local' or the peer that delivered it
    UNIQUE (origin, seq)
);
CREATE INDEX IF NOT EXISTS ix_events_order  ON events (lamport, origin, seq);
CREATE INDEX IF NOT EXISTS ix_events_entity ON events (entity_type, entity_id);

-- 2. Local node state (never replicated) -----------------------------------------------
CREATE TABLE IF NOT EXISTS peer_state (
    peer_id              TEXT PRIMARY KEY,
    peer_vv              TEXT NOT NULL DEFAULT '{}',   -- last version vector the peer reported
    last_attempt_at      TEXT,
    last_success_at      TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_attempt_at      REAL NOT NULL DEFAULT 0,      -- backoff (unix seconds)
    last_error           TEXT
);

CREATE TABLE IF NOT EXISTS sync_sessions (
    session_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id            TEXT NOT NULL,
    initiator          TEXT NOT NULL,      -- node that started the session
    trigger            TEXT NOT NULL,      -- manual | auto | lab
    started_at         TEXT NOT NULL,
    duration_ms        REAL,
    status             TEXT NOT NULL,      -- success | partial | failed | link_down
    events_pulled      INTEGER NOT NULL DEFAULT 0,
    events_pushed      INTEGER NOT NULL DEFAULT 0,
    duplicates         INTEGER NOT NULL DEFAULT 0,
    rejected           INTEGER NOT NULL DEFAULT 0,
    batches            INTEGER NOT NULL DEFAULT 0,
    retries            INTEGER NOT NULL DEFAULT 0,
    bytes_sent         INTEGER NOT NULL DEFAULT 0,  -- compressed bytes on the wire
    bytes_received     INTEGER NOT NULL DEFAULT 0,
    raw_bytes_sent     INTEGER NOT NULL DEFAULT 0,  -- uncompressed JSON size
    raw_bytes_received INTEGER NOT NULL DEFAULT 0,
    error              TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_peer ON sync_sessions (peer_id, session_id);

CREATE TABLE IF NOT EXISTS sim_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL,
    actor    TEXT NOT NULL,
    action   TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT ''   -- never contains sensitive person data
);

CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

-- 3. Materialized projections (rebuilt from events) ------------------------------------
CREATE TABLE IF NOT EXISTS teams (
    team_id    TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    specialty  TEXT NOT NULL,
    members    INTEGER NOT NULL,
    home_node  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS needs (
    need_id          TEXT PRIMARY KEY,
    category         TEXT NOT NULL,
    description      TEXT NOT NULL,
    quantity         INTEGER NOT NULL,
    unit             TEXT NOT NULL,
    priority         TEXT NOT NULL,
    location         TEXT NOT NULL,
    status           TEXT NOT NULL,
    responsible_team TEXT,
    origin           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    disputed_fields  TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS need_notes (
    note_id  TEXT PRIMARY KEY,
    need_id  TEXT NOT NULL REFERENCES needs (need_id) ON DELETE CASCADE,
    text     TEXT NOT NULL,
    origin   TEXT NOT NULL,
    at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supplies (
    supply_id    TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    kind         TEXT NOT NULL,        -- quantity | indivisible
    unit         TEXT NOT NULL,
    location     TEXT NOT NULL,
    condition    TEXT NOT NULL,
    total        INTEGER NOT NULL,
    available    INTEGER NOT NULL,     -- unallocated rights held by nodes
    reserved     INTEGER NOT NULL,
    distributed  INTEGER NOT NULL,
    unavailable  INTEGER NOT NULL,
    holder       TEXT,                 -- indivisible: node holding the exclusive token
    epoch        INTEGER,              -- indivisible: token generation
    status       TEXT NOT NULL,
    origin       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS allocation_rights (
    supply_id TEXT NOT NULL REFERENCES supplies (supply_id) ON DELETE CASCADE,
    node_id   TEXT NOT NULL,
    balance   INTEGER NOT NULL,
    PRIMARY KEY (supply_id, node_id)
);

CREATE TABLE IF NOT EXISTS rights_transfers (
    transfer_id TEXT PRIMARY KEY,
    supply_id   TEXT NOT NULL REFERENCES supplies (supply_id) ON DELETE CASCADE,
    from_node   TEXT NOT NULL,
    to_node     TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    epoch       INTEGER,
    request_id  TEXT,
    event_id    TEXT NOT NULL,
    at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_allocations (
    allocation_id TEXT PRIMARY KEY,
    supply_id     TEXT NOT NULL REFERENCES supplies (supply_id) ON DELETE CASCADE,
    node_id       TEXT NOT NULL,
    amount        INTEGER NOT NULL,
    target_type   TEXT NOT NULL,
    target_id     TEXT,
    status        TEXT NOT NULL,      -- reserved | distributed | released
    epoch         INTEGER,
    note          TEXT NOT NULL,
    event_id      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alloc_supply ON resource_allocations (supply_id);

CREATE TABLE IF NOT EXISTS rights_requests (
    request_id   TEXT PRIMARY KEY,
    supply_id    TEXT NOT NULL REFERENCES supplies (supply_id) ON DELETE CASCADE,
    requested_by TEXT NOT NULL,
    amount       INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    status       TEXT NOT NULL,       -- pending | fulfilled | declined
    at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shelters (
    shelter_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    location        TEXT NOT NULL,
    capacity        INTEGER NOT NULL,
    occupancy       INTEGER NOT NULL,
    available_space INTEGER NOT NULL,
    accessibility   TEXT NOT NULL,
    conditions      TEXT NOT NULL,
    status          TEXT NOT NULL,
    origin          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    disputed_fields TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS shelter_updates (
    event_id   TEXT PRIMARY KEY,
    shelter_id TEXT NOT NULL REFERENCES shelters (shelter_id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    delta      INTEGER,
    reason     TEXT NOT NULL,
    origin     TEXT NOT NULL,
    at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    person_id       TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    status_event_id TEXT NOT NULL,
    verification    TEXT NOT NULL,    -- unverified | verified | disputed
    reporting_team  TEXT NOT NULL,
    sensitive       TEXT NOT NULL,    -- JSON of AES-GCM ciphertexts only
    origin          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people_evidence (
    note_id    TEXT PRIMARY KEY,
    person_id  TEXT NOT NULL REFERENCES people (person_id) ON DELETE CASCADE,
    ciphertext TEXT NOT NULL,
    origin     TEXT NOT NULL,
    at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id         TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    priority        TEXT NOT NULL,
    location        TEXT NOT NULL,
    need_id         TEXT,
    assigned_team   TEXT,
    status          TEXT NOT NULL,
    origin          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT,
    disputed_fields TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS task_notes (
    note_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks (task_id) ON DELETE CASCADE,
    text    TEXT NOT NULL,
    origin  TEXT NOT NULL,
    at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id     TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,     -- concurrent_write | invariant
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    field           TEXT NOT NULL,
    policy          TEXT NOT NULL,     -- critical | lww | lattice | invariant
    status          TEXT NOT NULL,     -- open | resolved | auto_merged | acknowledged
    candidates      TEXT NOT NULL,     -- JSON list of competing writes with origin/lamport/value
    current_value   TEXT,
    resolution_kind TEXT,              -- explicit | superseded | auto_lww | auto_lattice
    resolved_by     TEXT,
    rationale       TEXT,
    detected_at     TEXT NOT NULL,
    resolved_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_conflicts_status ON conflicts (status);

CREATE TABLE IF NOT EXISTS rejected_events (
    event_id TEXT PRIMARY KEY,
    origin   TEXT NOT NULL,
    type     TEXT NOT NULL,
    reason   TEXT NOT NULL
);
"""

PROJECTION_TABLES = [
    "need_notes", "task_notes", "people_evidence", "shelter_updates", "allocation_rights",
    "rights_transfers", "resource_allocations", "rights_requests", "conflicts", "rejected_events",
    "needs", "tasks", "people", "shelters", "supplies", "teams",
]


class Database:
    """Thin thread-safe wrapper: one connection guarded by a re-entrant lock."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                          (str(SCHEMA_VERSION),))

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def transaction(self):
        return _Tx(self)

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value) -> None:
        self.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, str(value)))

    def bump(self, name: str, by: int = 1) -> None:
        self.execute("INSERT INTO counters (name, value) VALUES (?, ?) "
                     "ON CONFLICT(name) DO UPDATE SET value = value + excluded.value", (name, by))

    def counters(self) -> dict[str, int]:
        return {r["name"]: r["value"] for r in self.query("SELECT name, value FROM counters")}

    def close(self) -> None:
        with self.lock:
            self.conn.close()


class _Tx:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db.lock.release()
        return False
