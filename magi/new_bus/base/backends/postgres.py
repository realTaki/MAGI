"""PostgreSQL Backend. Same record protocol as SQLite."""

from __future__ import annotations

from sqlalchemy import create_engine

from ._sql import SqlBackend


class PostgresBackend(SqlBackend):
    def __init__(self, dsn: str) -> None:
        super().__init__(create_engine(dsn))
