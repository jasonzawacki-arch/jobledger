"""SQLite storage. One file, no server. The schema version lives in PRAGMA user_version."""
from __future__ import annotations

import sqlite3
import threading

SCHEMA = [
    # version 1
    """
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT NOT NULL,
        title TEXT NOT NULL,
        location TEXT NOT NULL DEFAULT '',
        pay TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        posted_on TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        stage INTEGER NOT NULL DEFAULT 0 CHECK (stage BETWEEN 0 AND 4),
        max_stage INTEGER NOT NULL DEFAULT 0 CHECK (max_stage BETWEEN 0 AND 4),
        closed_reason TEXT CHECK (closed_reason IN ('rejected', 'withdrawn', 'ghosted')),
        follow_up_on TEXT,
        stage_changed_at REAL NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        at REAL NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('created', 'stage', 'note', 'closed', 'reopened', 'follow_up')),
        text TEXT NOT NULL
    );
    CREATE INDEX events_job ON events (job_id, at);
    """,
    # version 2: the resume, kept as the text the user pasted (one row)
    """
    CREATE TABLE resume (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        text TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
    # version 3: people to remember, and an interview prep checklist, both per job
    """
    CREATE TABLE contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        last_contacted_on TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX contacts_job ON contacts (job_id);
    CREATE TABLE prep_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        done INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
        position INTEGER NOT NULL
    );
    CREATE INDEX prep_job ON prep_items (job_id, position);
    """,
]


class Database:
    """A small wrapper that serializes access so the web server's threads can share one connection."""

    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    def migrate(self) -> None:
        with self.lock:
            v = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if v > len(SCHEMA):
                raise RuntimeError(f"Database is from a newer JobLedger (schema {v}). Update the app.")
            for i in range(v, len(SCHEMA)):
                self.conn.executescript("BEGIN;" + SCHEMA[i] + f"PRAGMA user_version = {i + 1};COMMIT;")

    def close(self) -> None:
        with self.lock:
            self.conn.close()
