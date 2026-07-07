"""Tiny KV helpers on top of the ``settings`` table.

C0 / C1.0b: this is the only writer of bot tokens, channel config
and similar runtime values. C1.1 will layer a SQLAlchemy model on top
of the same table; the helpers here are the synchronous fall-back that
the FastAPI endpoints use for the time being.

Concurrency model
=================

There's exactly ONE call site that opens sqlite3 — this file.
The Telegram bot thread and the FastAPI event loop both go
through ``state_get`` / ``state_set`` / ``state_delete`` here.
Two design choices keep that race-safe:

1. **One open per call.** We ``with sqlite3.connect(...)`` for
   every helper, so each request / poll cycle gets a fresh
   connection. The connection is closed (and its in-flight WAL
   frame flushed) on context exit. No long-lived connection
   means no "someone forgot to close it" leak.

2. **WAL + busy_timeout on every connection.** The ``with``
   helper applies the same PRAGMAs ``init_sqlite`` sets, so a
   connection opened mid-flight (e.g. the TG thread reading
   while a FastAPI handler is writing) is just as well-behaved
   as the boot-time connection. WAL means readers and writers
   don't block each other; ``busy_timeout`` makes the rare
   same-row race auto-retry for up to 5s before erroring.

For multi-worker uvicorn later, each worker would have its own
process and its own connections; SQLite's file-level locking
serialises them across processes. No change needed here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _db_path(state_dir: str) -> Path:
    return Path(state_dir) / "magi.db"


def _connect(state_dir: str) -> sqlite3.Connection:
    """Open a connection with the same PRAGMAs ``init_sqlite`` sets.

    Centralizing this here means the TG thread, the FastAPI
    request handler, and any future caller all get identical
    concurrency behavior — readers and writers don't block each
    other, and a same-row race retries for ``busy_timeout`` ms
    instead of raising ``OperationalError``.
    """
    conn = sqlite3.connect(str(_db_path(state_dir)))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def state_get(state_dir: str, key: str) -> str | None:
    """Return the value for ``key`` or ``None`` if unset."""
    with _connect(state_dir) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row else None


def state_set(state_dir: str, key: str, value: str) -> None:
    """Upsert ``key=value``. Touches ``updated_at`` on every write."""
    with _connect(state_dir) as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value      = excluded.value,
                updated_at = datetime('now')
            """,
            (key, value),
        )
        conn.commit()


def state_delete(state_dir: str, key: str) -> None:
    """Remove a key. No-op if it doesn't exist."""
    with _connect(state_dir) as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()