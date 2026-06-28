"""Structured audit log + content store (SQLite).

Two tables:

* ``audit_log`` — append-only record of every decision and (from Milestone 5) every
  appeal. Each row stores the full structured entry as JSON in ``payload`` plus a few
  promoted columns for easy querying.
* ``content`` — current status of each submission (``classified`` -> ``under_review``).

SQLite is built into Python, so there is nothing extra to install. The DB file is
git-ignored; it is regenerated on first run via ``init_db()``.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "provenance_guard.db"


def now_iso():
    """Current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_type  TEXT NOT NULL,           -- 'classification' | 'appeal'
                content_id  TEXT NOT NULL,
                creator_id  TEXT,
                timestamp   TEXT NOT NULL,
                attribution TEXT,
                confidence  REAL,
                payload     TEXT NOT NULL             -- full structured entry as JSON
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS content (
                content_id TEXT PRIMARY KEY,
                creator_id TEXT,
                status     TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def create_content(content_id, creator_id, status, timestamp):
    with _conn() as c:
        c.execute(
            "INSERT INTO content (content_id, creator_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (content_id, creator_id, status, timestamp, timestamp),
        )


def update_content_status(content_id, status, timestamp):
    """Used by the appeals workflow (Milestone 5). Returns True if a row was updated."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE content SET status = ?, updated_at = ? WHERE content_id = ?",
            (status, timestamp, content_id),
        )
        return cur.rowcount > 0


def get_content(content_id):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM content WHERE content_id = ?", (content_id,)
        ).fetchone()
        return dict(row) if row else None


def _append(entry_type, entry):
    """Append one structured entry to the audit log."""
    with _conn() as c:
        c.execute(
            "INSERT INTO audit_log (entry_type, content_id, creator_id, timestamp, "
            "attribution, confidence, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry_type,
                entry.get("content_id"),
                entry.get("creator_id"),
                entry.get("timestamp"),
                entry.get("attribution"),
                entry.get("confidence"),
                json.dumps(entry),
            ),
        )


def log_classification(entry):
    _append("classification", entry)


def log_appeal(entry):
    _append("appeal", entry)


def get_log(limit=50):
    """Return the most recent audit entries (newest first) as a list of dicts.

    Each returned dict is the full stored payload with a ``type`` field added so a
    reader can tell classifications and appeals apart at a glance.
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT entry_type, payload FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    entries = []
    for row in rows:
        entry = json.loads(row["payload"])
        entry["type"] = row["entry_type"]
        entries.append(entry)
    return entries
