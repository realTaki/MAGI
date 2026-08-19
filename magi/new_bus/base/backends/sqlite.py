"""SQLite Backend. Default local store for a single MAGI process."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event

from ._sql import SqlBackend


class _SQLiteDriver:
    placeholder = "?"
    id_column = "id INTEGER PRIMARY KEY AUTOINCREMENT"

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
        path = Path(path)
        engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        super().__init__(_SQLiteDriver(path), engine=engine)
