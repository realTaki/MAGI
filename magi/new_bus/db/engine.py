"""引擎工厂 — 根据 database_url 自动适配 SQLite / PostgreSQL。

用法::

    from magi.new_bus.db import EngineFactory

    factory = EngineFactory("sqlite:///data/magi.db")
    # 或
    factory = EngineFactory("postgresql://user:pass@localhost:5432/magi")

    with factory.session() as s:
        ...
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from magi.new_bus.db.base import Base

_SQLITE_PREFIX = "sqlite:///"
_PG_PREFIX = "postgresql://"


class EngineFactory:
    """根据 database_url 创建引擎，统一 SQLite 和 PG 的差异。

    SQLite:  文件路径，加 WAL / foreign_keys / busy_timeout / BEGIN IMMEDIATE。
    PG:      连接 URL，开箱即用。
    """

    def __init__(self, database_url: str):
        self._url = database_url
        self._dialect = "sqlite" if database_url.startswith(_SQLITE_PREFIX) else "postgresql"
        self._engine = self._build_engine()
        self._session_factory = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )

    # -- public ------------------------------------------------------------

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def dialect(self) -> str:
        return self._dialect

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        s = self._session_factory()
        try:
            yield s
        finally:
            s.close()

    def create_all(self) -> None:
        Base.metadata.create_all(self._engine)

    # -- internal ---------------------------------------------------------

    def _build_engine(self) -> Engine:
        if self._dialect == "sqlite":
            engine = create_engine(
                self._url,
                connect_args={"check_same_thread": False},
            )
            self._apply_sqlite_pragmas(engine)
            self._apply_begin_immediate(engine)
        else:
            engine = create_engine(self._url)
        return engine

    @staticmethod
    def _apply_sqlite_pragmas(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=5000")
            finally:
                cur.close()

    @staticmethod
    def _apply_begin_immediate(engine: Engine) -> None:
        @event.listens_for(engine, "begin")
        def _on_begin(dbapi_conn):
            dbapi_conn.exec_driver_sql("BEGIN IMMEDIATE")


# -- 模块级快捷方式 ---------------------------------------------------------

_factory: EngineFactory | None = None


def get_engine(database_url: str | None = None) -> Engine:
    global _factory
    if _factory is None:
        if database_url is None:
            raise ValueError("首次调用 get_engine 必须提供 database_url")
        _factory = EngineFactory(database_url)
    return _factory.engine


def get_session() -> Generator[Session, None, None]:
    if _factory is None:
        raise RuntimeError("EngineFactory 尚未初始化，请先调用 get_engine(url)")
    s = _factory._session_factory()
    try:
        yield s
    finally:
        s.close()


@contextmanager
def open_session() -> Generator[Session, None, None]:
    if _factory is None:
        raise RuntimeError("EngineFactory 尚未初始化，请先调用 get_engine(url)")
    s = _factory._session_factory()
    try:
        yield s
    finally:
        s.close()
