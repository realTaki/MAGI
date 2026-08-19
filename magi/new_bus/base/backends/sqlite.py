"""SQLite Backend. Default local store for a single MAGI process."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from ._sql import SqlBackend


def _sqlite_engine(url: str, *, static: bool = False):
    options: dict = {"connect_args": {"check_same_thread": False}}
    if static:
        options["poolclass"] = StaticPool
    engine = create_engine(url, **options)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if not static:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


class SQLiteBackend(SqlBackend):
    def __init__(self, path: str | Path | None = None, *, memory: bool = False) -> None:
        if memory or path is None:
            super().__init__(_sqlite_engine("sqlite://", static=True))
            return
        super().__init__(_sqlite_engine(f"sqlite:///{Path(path)}"))
