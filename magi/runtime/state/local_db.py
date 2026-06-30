"""SQLite local store — created on every MAGI container boot.

Independent of role: Adam uses SQLite for its (small / dev) system-of-record
state and Eve uses it for personal working state. A Postgres store lands
in C1 alongside the ORM; this module is the SQLite counterpart and stays
useful for Eve forever.

For C0 the file just contains a ``meta`` table for schema_version
tracking. C1+ (via SQLAlchemy + Alembic) will add real tables — the
schema_version row is the hand-off point.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

META_SCHEMA_VERSION = "schema_version"
INITIAL_SCHEMA_VERSION = "0"


def init_sqlite(state_dir: str) -> Path:
    """Create the SQLite file under ``state_dir`` if missing.

    Idempotent — safe to call on every container boot. Returns the
    absolute path to the database file so callers can log it.

    Creates one table (``meta``) holding key/value rows. The first row
    is ``schema_version = "0"`` so Alembic can take over in C1 without
    re-creating the file.
    """
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)

    db_path = directory / "magi.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            (META_SCHEMA_VERSION, INITIAL_SCHEMA_VERSION),
        )
        conn.commit()

    return db_path