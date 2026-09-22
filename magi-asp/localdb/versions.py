"""Ordered, idempotent schema upgrades for the ASP sqlite file."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

Migration = Callable[[sqlite3.Connection], None]


def _version_1(connection: sqlite3.Connection) -> None:
    """ASP-owned settings. Session rows land here in a later revision."""
    connection.executescript(
        """
        CREATE TABLE asp_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
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
