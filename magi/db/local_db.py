"""SQLite local store — created on every MAGI container boot.

Independent of role: Adam uses SQLite for its (small / dev) system-of-record
state and Eve uses it for personal working state. A Postgres store lands
in C1 alongside the ORM; this module is the SQLite counterpart and stays
useful for Eve forever.

The file bootstrap creates only the legacy ``meta`` table for schema-version
hand-off. Application tables are created by Alembic in ``init_orm``; the
``settings`` table is no longer created or accessed through this raw-SQL
bootstrap path.
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

    Creates one table (``meta``) holding key/value rows. The first row is
    ``schema_version = "0"`` for pre-Alembic compatibility; active schema
    versioning lives in Alembic's ``alembic_version`` table.

    PRAGMA ordering matters:

      1. ``journal_mode=WAL`` (committed before any other PRAGMA so
         it sticks; SQLite resets ``synchronous`` when ``journal_mode``
         changes, see design §15).
      2. ``busy_timeout=5000``.
      3. ``synchronous=NORMAL`` *after* a commit so the value lands
         on the persisted file, not just the in-connection cache.
    """
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)

    db_path = directory / "magi.db"
    with sqlite3.connect(str(db_path)) as conn:
        # WAL mode = readers don't block writers, writers don't
        # block readers. Crucial for our setup: the FastAPI
        # event loop + the Telegram bot thread both hit the DB through
        # SQLAlchemy. Without WAL a long-ish read could stall an
        # in-flight write and vice versa. WAL is also more
        # crash-safe (the -wal sidecar is fsync'd instead of
        # overwriting the main file).
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
        conn.commit()
        # ``synchronous=NORMAL`` (design §15) pairs with WAL: the WAL
        # frame is fsync'd at commit but the main DB file is not.
        # SQLite resets ``synchronous`` whenever ``journal_mode``
        # changes, so it must be re-asserted AFTER the first commit
        # to take effect on the persisted file.
        conn.execute("PRAGMA synchronous=NORMAL")

    return db_path