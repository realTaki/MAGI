"""Ordered, idempotent schema upgrades for the one Webapp SQLite file."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

Migration = Callable[[sqlite3.Connection], None]


def _version_1(connection: sqlite3.Connection) -> None:
    """Create Webapp-owned configuration and local conversation tables."""
    connection.executescript(
        """
        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            magi_id TEXT NOT NULL,
            remote_id TEXT,
            title TEXT NOT NULL DEFAULT '',
            sync_cursor TEXT,
            remote_updated_at INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE UNIQUE INDEX conversations_remote_id
            ON conversations(magi_id, remote_id)
            WHERE remote_id IS NOT NULL;
        CREATE INDEX conversations_by_magi_updated
            ON conversations(magi_id, updated_at DESC);
        """
    )


MIGRATIONS: tuple[Migration, ...] = (_version_1,)


def apply_migrations(connection: sqlite3.Connection) -> None:
    """Apply every missing version in a single atomic transaction each."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at INTEGER NOT NULL
        )
        """
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    for version, migration in enumerate(MIGRATIONS, start=1):
        if version in applied:
            continue
        with connection:
            migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, unixepoch() * 1000)",
                (version,),
            )

