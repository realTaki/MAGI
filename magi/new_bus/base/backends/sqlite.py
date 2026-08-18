"""SQLite Backend. Default local store for a single MAGI process."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ._sql import SqlBackend


class _SQLiteDriver:
    placeholder = "?"

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


class SQLiteBackend(SqlBackend):
    def __init__(self, path: str | Path) -> None:
        super().__init__(_SQLiteDriver(path))
