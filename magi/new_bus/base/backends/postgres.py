"""PostgreSQL Backend. Same record protocol as SQLite."""

from __future__ import annotations

from ...errors import BackendError
from ._sql import SqlBackend


class _PostgresDriver:
    placeholder = "%s"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def connect(self):
        try:
            import psycopg
        except ImportError as exc:
            raise BackendError("psycopg is required for PostgresBackend") from exc
        return psycopg.connect(self.dsn)


class PostgresBackend(SqlBackend):
    def __init__(self, dsn: str) -> None:
        super().__init__(_PostgresDriver(dsn))
