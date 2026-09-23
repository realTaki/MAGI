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


def _version_2(connection: sqlite3.Connection) -> None:
    """Durable relay state and per-recipient message acknowledgements."""
    connection.executescript(
        """
        CREATE TABLE asp_agents (handle TEXT PRIMARY KEY, record_json TEXT NOT NULL);
        CREATE TABLE asp_sessions (
            id TEXT PRIMARY KEY,
            record_json TEXT NOT NULL,
            next_sequence INTEGER NOT NULL
        );
        CREATE TABLE asp_participants (
            session_id TEXT NOT NULL,
            handle TEXT NOT NULL,
            record_json TEXT NOT NULL,
            PRIMARY KEY (session_id, handle)
        );
        CREATE TABLE asp_events (
            session_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (session_id, sequence)
        );
        CREATE TABLE asp_message_recipients (
            event_id TEXT NOT NULL,
            handle TEXT NOT NULL,
            acked INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (event_id, handle),
            FOREIGN KEY (event_id) REFERENCES asp_events(event_id) ON DELETE CASCADE
        );
        CREATE TABLE asp_delivery_acks (
            session_id TEXT NOT NULL,
            handle TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            PRIMARY KEY (session_id, handle)
        );
        CREATE TABLE asp_message_keys (
            session_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            key TEXT NOT NULL,
            message_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            PRIMARY KEY (session_id, sender, key)
        );
        CREATE TABLE asp_session_keys (
            creator TEXT NOT NULL,
            key TEXT NOT NULL,
            session_id TEXT NOT NULL,
            sequence INTEGER,
            PRIMARY KEY (creator, key)
        );
        """
    )


MIGRATIONS: tuple[Migration, ...] = (_version_1, _version_2)


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
