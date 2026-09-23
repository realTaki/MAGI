"""The durable SQLite connection owned by a running ASP server process."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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
