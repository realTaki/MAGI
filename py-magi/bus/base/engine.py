"""Engine + Session. SQLAlchemy talks to SQLite and PostgreSQL."""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .time import dump_json


class EngineFactory:
    """One engine and sessionmaker. Books and JobBoards each hold one."""

    def __init__(self, database_url: str, *, memory: bool = False) -> None:
        self._url = database_url
        driver = make_url(database_url).drivername
        if driver.startswith("sqlite"):
            self._dialect = "sqlite"
        elif driver.startswith("postgresql"):
            self._dialect = "postgresql"
        else:
            raise ValueError(f"BUS storage only supports SQLite or PostgreSQL URLs; got {driver!r}")
        self._memory = memory or database_url == "sqlite://"
        self._lock = threading.RLock()
        self._engine = self._build_engine()
        self._sessions = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def dialect(self) -> str:
        return self._dialect

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        with self._lock:
            item = self._sessions()
            try:
                yield item
            finally:
                item.close()

    def close(self) -> None:
        self._engine.dispose()

    def _build_engine(self) -> Engine:
        if self._dialect != "sqlite":
            return create_engine(self._url, json_serializer=dump_json)
        options: dict = {
            "connect_args": {"check_same_thread": False},
            "json_serializer": dump_json,
        }
        if self._memory:
            options["poolclass"] = StaticPool
        engine = create_engine(self._url, **options)

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _record) -> None:
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                if not self._memory:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()

        return engine


class SQLiteBackend(EngineFactory):
    def __init__(self, path: str | Path | None = None, *, memory: bool = False) -> None:
        if memory or path is None:
            super().__init__("sqlite://", memory=True)
            return
        super().__init__(f"sqlite:///{Path(path)}")


class PostgresBackend(EngineFactory):
    def __init__(self, dsn: str) -> None:
        super().__init__(dsn)
