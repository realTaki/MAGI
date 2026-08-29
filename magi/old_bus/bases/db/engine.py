"""引擎工厂 — 根据 database_url 自动适配 SQLite / PostgreSQL。

支持多实例共存：local SQLite + MAGIS SQLite 或 PostgreSQL 各持一个 EngineFactory::

    from magi.bus.bases.db.engine import build_local_factory, build_magis_factory

    local = build_local_factory("/var/magi/state")          # → EngineFactory("sqlite:////var/magi/state/magi.db")
    magis = build_magis_factory("postgresql://user:pw@db/magis")

    with local.session() as s:
        ...

每个 ``EngineFactory`` 拥有自己的 ``Engine`` 和 ``sessionmaker``，
所有 ORM 类注册到同一份 ``magi.bus.bases.db.base.Base.metadata``。
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from magi.old_bus.bases.db.base import Base


class EngineFactory:
    """根据 database_url 创建引擎，统一 SQLite 和 PG 的差异。

    SQLite:  文件路径，加 WAL / foreign_keys / busy_timeout / BEGIN IMMEDIATE。
    PG:      连接 URL，开箱即用。

    可以创建多个 EngineFactory 实例（一个 local，一个 magis），
    每个实例独立维护 Engine + sessionmaker，但共享同一份
    ``magi.bus.bases.db.base.Base.metadata``。
    """

    def __init__(self, database_url: str):
        self._url = database_url
        drivername = make_url(database_url).drivername
        if drivername.startswith("sqlite"):
            self._dialect = "sqlite"
        elif drivername.startswith("postgresql"):
            self._dialect = "postgresql"
        else:
            raise ValueError(
                f"BUS storage only supports SQLite or PostgreSQL URLs; got {drivername!r}"
            )
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

    @property
    def url(self) -> str:
        return self._url

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        s = self._session_factory()
        try:
            yield s
        finally:
            s.close()

    def create_all(self) -> None:
        """Create all tables in this engine's database.

        Safe to call on a pre-existing database — SQLAlchemy skips
        tables that already exist.
        """
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


# -- 便利构造器 -----------------------------------------------------------


def build_local_factory(state_dir: str) -> EngineFactory:
    """Build the local SQLite ``EngineFactory`` for one runtime's state dir.

    The SQLite file lives at ``<state_dir>/magi.db``.
    """
    db_path = Path(state_dir) / "magi.db"
    return EngineFactory(f"sqlite:///{db_path}")


def build_magis_factory(database_url: str) -> EngineFactory:
    """Build the shared MAGIS ``EngineFactory`` from a SQLite or PostgreSQL URL.

    A SQLite URL identifies a per-MAGIS database file; a PostgreSQL URL
    identifies one database in a shared PostgreSQL service.  SQLite-specific
    pragmas are selected by :class:`EngineFactory` automatically.
    """
    return EngineFactory(database_url)
