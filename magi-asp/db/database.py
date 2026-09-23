"""The durable SQLite connection owned by a running ASP server process."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .versions import apply_migrations


def default_data_dir() -> Path:
    return Path.home() / ".magi" / "asp"


def default_database_path() -> Path:
    return default_data_dir() / "asp.sqlite"


class LocalDatabase:
    """Open, migrate and close the ASP server's SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def open(self) -> None:
        if self.connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        legacy = Path.home() / ".magi" / "asp.sqlite"
        if self.path == default_database_path() and not self.path.exists() and legacy.exists():
            with sqlite3.connect(legacy) as previous, sqlite3.connect(self.path) as current:
                previous.backup(current)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        apply_migrations(connection)
        self.connection = connection

    def close(self) -> None:
        if self.connection is None:
            return
        self.connection.close()
        self.connection = None

    def get_setting(self, key: str) -> Any | None:
        """Read one ASP-owned setting; ``None`` when it was never written."""
        connection = self._connection()
        row = connection.execute(
            "SELECT value_json FROM asp_settings WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else json.loads(row["value_json"])

    def set_setting(self, key: str, value: Any) -> None:
        """Write one ASP-owned setting (JSON), replacing any previous value."""
        connection = self._connection()
        with connection:
            connection.execute(
                """
                INSERT INTO asp_settings (key, value_json, updated_at)
                VALUES (?, ?, unixepoch() * 1000)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value)),
            )

    def delete_setting(self, key: str) -> None:
        connection = self._connection()
        with connection:
            connection.execute("DELETE FROM asp_settings WHERE key = ?", (key,))

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("LocalDatabase is not open")
        return self.connection
