"""SQLAlchemy ORM models + bootstrap.

Lives in the same SQLite file as the hand-rolled ``settings`` KV
store (C0). The two coexist: ``settings`` and ``meta`` are written
by the raw-SQL helpers in ``local_db.py`` / ``settings.py``;
everything else uses SQLAlchemy via this module.

We deliberately use ``Base.metadata.create_all`` (not Alembic) for
C1.1 — the schema is small, the tables are new, and adding Alembic
now would double the surface area. The first Alembic baseline
migration lands when the schema stabilises (probably end of C1.3).
Once Alembic is in, this ``init_orm`` becomes the no-op it
already is at runtime — only ``alembic upgrade head`` touches
the schema.

Thread-safety: a single ``Engine`` shared across the process,
``Session`` per request (FastAPI dependency). The engine is
configured with ``check_same_thread=False`` so the TG bot thread
and the uvicorn event loop can both issue queries without
tripping over the default. WAL mode (set in ``init_sqlite``)
handles writer/reader concurrency.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Generator

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

logger = logging.getLogger("magi.runtime.state.orm")

# Default ``state_dir`` matches the in-container location; tests
# override via the ``MAGI_STATE_DIR`` env var.
_DEFAULT_STATE_DIR = "/workspace/memories"


class Base(DeclarativeBase):
    """Shared declarative base for all ORM tables."""


class Employee(Base):
    """A person in the company. For C1.1 we only need enough
    fields for the "department manager" picker; full lifecycle
    (email, TG binding, quiet hours, status) lands with C1.2 +
    C1.3 + C2.

    Schema kept minimal on purpose — the table doubles as the
    target of ``Department.manager_id`` and as a future join
    target for the directory / EVE-assignment tables.
    """

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    # C2 will add telegram_id, status, quiet_hours, etc.

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # The department this employee leads, if any. Backref from
    # ``Department.manager`` so we can ask the department who
    # its lead is without a second query.
    led_department: Mapped["Department | None"] = relationship(
        back_populates="manager",
        foreign_keys="Department.manager_id",
    )

    def __repr__(self) -> str:
        return f"Employee(id={self.id}, name={self.name!r})"


class Department(Base):
    """A node in the company org tree.

    The tree is encoded by ``parent_id``: top-level departments
    have ``parent_id = NULL``; every other department's parent
    is another department in the same table. Cycles are
    prevented at the API layer (POST/PATCH refuse a parent that
    would close a loop).

    ``manager_id`` references ``employees.id`` and is nullable —
    a department can exist without a manager assigned yet.
    """

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Self-referential tree. ``remote_side=id`` is the magic that
    # tells SQLAlchemy which side of the parent_id FK is the
    # "many" side, so ``children`` is a list of departments
    # rather than a back to the parent.
    children: Mapped[list["Department"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    parent: Mapped["Department | None"] = relationship(
        back_populates="children",
        remote_side="Department.id",
    )

    manager: Mapped["Employee | None"] = relationship(
        back_populates="led_department",
        foreign_keys=[manager_id],
    )

    def __repr__(self) -> str:
        return f"Department(id={self.id}, name={self.name!r}, parent_id={self.parent_id})"


# -- bootstrap --------------------------------------------------------------

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _state_dir_from_env() -> str:
    return os.environ.get("MAGI_STATE_DIR", _DEFAULT_STATE_DIR)


def get_engine() -> Engine:
    """Return the process-wide engine, initialising it on first call.

    The engine is created lazily so importing this module is cheap
    (no DB connection until the first request). After the first
    call, every subsequent call returns the same engine.
    """
    global _engine, _SessionLocal
    if _engine is None:
        state_dir = Path(_state_dir_from_env())
        state_dir.mkdir(parents=True, exist_ok=True)
        db_path = state_dir / "magi.db"
        # ``check_same_thread=False`` lets the TG bot thread and
        # the FastAPI handler thread share the engine. SQLAlchemy
        # serialises access internally so this is safe.
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        _SessionLocal = sessionmaker(
            bind=_engine, autocommit=False, autoflush=False, expire_on_commit=False
        )
    return _engine


def init_orm(state_dir: str | None = None) -> Engine:
    """Create all ORM tables and return the engine.

    Idempotent — ``create_all`` is a no-op for tables that
    already exist, so calling on every boot is safe. Run from
    ``magi.node.run`` alongside ``init_sqlite``; the two target
    the same file but different tables, so they don't conflict.
    """
    engine = get_engine()
    if state_dir is not None:
        # Honour an explicit override (mostly for tests).
        os.environ["MAGI_STATE_DIR"] = state_dir
    Base.metadata.create_all(engine)
    logger.info(
        "orm initialised",
        extra={"tables": sorted(Base.metadata.tables.keys())},
    )
    return engine


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency — yield a session, close on request end.

    Usage in a route::

        from fastapi import Depends
        from magi.runtime.state.orm import get_session

        @router.get(...)
        def list_departments(session: Session = Depends(get_session)):
            ...
    """
    if _SessionLocal is None:
        # The first request arrives before ``init_orm`` was called
        # (shouldn't happen in production — boot always runs init
        # first — but be defensive). Initialise on demand.
        init_orm()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()
