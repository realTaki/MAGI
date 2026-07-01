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
        # WAL mode = readers don't block writers, writers don't
        # block readers. Crucial for our setup: the FastAPI
        # event loop + the Telegram bot thread both hit the DB
        # via magi/runtime/state/settings.py. Without WAL a
        # long-ish read could stall an in-flight write and vice
        # versa. WAL is also more crash-safe (the -wal sidecar
        # is fsync'd instead of overwriting the main file).
        conn.execute("PRAGMA journal_mode=WAL")
        # busy_timeout is the per-connection grace period before
        # SQLite raises "database is locked". 5s is the stdlib
        # default but we set it explicitly so the value is
        # visible in the schema-design history. With WAL, this
        # is rarely needed, but it's cheap insurance.
        conn.execute("PRAGMA busy_timeout=5000")
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
        # ``settings`` is a small KV store for runtime config — channel
        # bot tokens, verified flags, etc. Kept in SQLite (not env)
        # because the webui writes to it at runtime and env is
        # read-only. C1.1's ORM/Alembic pass will add a real model
        # on top of this table; the schema here is deliberately
        # minimal so that hand-off is a no-op (Alembic baseline sees
        # the table as already created).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()

    return db_path