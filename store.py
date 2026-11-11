"""Persistent case store — SQLite (WAL), append-only audit.

One store for both agents and the gate: incidents, linked reports,
reservations, plans, approvals, dispatches, acknowledgements and the audit
log live here so state survives refresh and restart (blueprint: the queue's
state is a property of the database, never of the page).

Audit rows are append-only, enforced in the database itself: BEFORE UPDATE /
BEFORE DELETE triggers abort any attempt (test T06).
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locations (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    landmark TEXT,
    flood_prone INTEGER NOT NULL,
    confined INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS crews (
    crew_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    home TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    lang TEXT,
    text TEXT NOT NULL,
    location_hint TEXT,
    people_affected INTEGER,
    received_at TEXT NOT NULL,
    incident_id TEXT,
    parse_mode TEXT
);
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    location_code TEXT,
    hazard_type TEXT,
    facts TEXT NOT NULL,
    missing_info TEXT NOT NULL,
    score INTEGER,
    band TEXT,
    factor_breakdown TEXT,
    reason TEXT,
    provisional INTEGER NOT NULL DEFAULT 0,
    medical_override INTEGER NOT NULL DEFAULT 0,
    queue_rank INTEGER,
    created_seq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS incident_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    relation TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    assignments TEXT NOT NULL,
    checks TEXT NOT NULL,
    matching_trace TEXT,
    based_on_version INTEGER NOT NULL,
    plan_hash TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'proposed'
);
CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    crew_id TEXT NOT NULL,
    people_assigned INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'held'
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    based_on_version INTEGER NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispatches (
    dispatch_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    crew_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'sent',
    task_message TEXT,
    sent_at TEXT,
    ack_at TEXT,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ---------- meta / resource version ----------
    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def resource_version(self):
        v = self.get_meta("resource_version")
        return int(v) if v is not None else 0

    def bump_resource_version(self):
        v = self.resource_version() + 1
        self.set_meta("resource_version", str(v))
        return v

    # ---------- audit ----------
    def audit(self, event, actor, payload):
        cur = self.conn.execute(
            "INSERT INTO audit(ts, event, actor, payload) VALUES(?, ?, ?, ?)",
            (now_iso(), event, actor, json.dumps(payload, ensure_ascii=False)))
        return cur.lastrowid

    def audit_rows(self, limit=300):
        rows = self.conn.execute(
            "SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------- generic row helpers ----------
    def one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    def close(self):
        with self.lock:
            self.conn.close()
