"""Tiny KV helpers on top of the ``settings`` table.

C0 / C1.0b: this is the only writer of bot tokens, channel config
and similar runtime values. C1.1 will layer a SQLAlchemy model on top
of the same table; the helpers here are the synchronous fall-back that
the FastAPI endpoints use for the time being.

Thread-safety: FastAPI runs uvicorn in a single event loop; SQLite
calls are quick (local file) and don't block long enough to cause
issues. If we move to multi-worker uvicorn later, wrap reads/writes
in ``asyncio.to_thread``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _db_path(state_dir: str) -> Path:
    return Path(state_dir) / "magi.db"


def state_get(state_dir: str, key: str) -> str | None:
    """Return the value for ``key`` or ``None`` if unset."""
    with sqlite3.connect(str(_db_path(state_dir))) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row else None


def state_set(state_dir: str, key: str, value: str) -> None:
    """Upsert ``key=value``. Touches ``updated_at`` on every write."""
    with sqlite3.connect(str(_db_path(state_dir))) as conn:
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
    with sqlite3.connect(str(_db_path(state_dir))) as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()