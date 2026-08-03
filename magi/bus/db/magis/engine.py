
"""Public database access for a MAGIS.

Each MAGI keeps a private SQLite database for its memory, sessions and local
work.  Organisation facts live in the PostgreSQL database of its direct
MAGIS.  The sole runtime discovery input is ``MAGIS_DATABASE_URL``; Kubernetes
mounts it from the MAGIS database Secret, rather than passing provider or
instruction values as environment variables.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _url() -> str | None:
    return os.environ.get("MAGIS_DATABASE_URL") or None


def get_magis_engine() -> Engine:
    """Return this node's direct MAGIS public database engine.

    Tests and legacy-local development deliberately fall back to the node
    SQLite engine.  Production deployments always inject
    ``MAGIS_DATABASE_URL`` from the direct MAGIS Secret.
    """
    global _engine, _session_factory
    url = _url()
    if url is None:
        from magi.bus._persistence.engine import get_engine

        return get_engine()
    if _engine is None or str(_engine.url) != url:
        if _engine is not None:
            _engine.dispose()
        _engine = create_engine(url, pool_pre_ping=True, future=True)
        _session_factory = sessionmaker(bind=_engine, autocommit=False, autoflush=False, expire_on_commit=False)
    return _engine


def init_magis_public_db(*, seed_root: bool = False) -> Engine:
    """Create public organisation tables and optionally seed Genesis.

    The public schema has a deliberately narrow table set.  Private models
    are never created in PostgreSQL.
    """
    from magi.bus.models.local.control_plane import ControlOperator, ControlSetting
    from magi.bus.models.magis.eve_runtime import EveRuntime
    from magi.bus.models.magis.magic import MAGIC
    from magi.bus.models.magis.magis import MAGIS
    from magi.bus.models.magis.magis_admin import MAGISAdmin
    from magi.bus.models.magis.magis_membership import MAGISMembership, MAGISRole

    engine = get_magis_engine()
    if _url() is not None:
        # SQLAlchemy sorts the FK dependencies (magic before magis) for us.
        MAGIS.metadata.create_all(
            engine,
            tables=[MAGIC.__table__, MAGIS.__table__, MAGISRole.__table__, MAGISMembership.__table__, MAGISAdmin.__table__, EveRuntime.__table__, ControlSetting.__table__, ControlOperator.__table__],
        )
    if seed_root:
        from magi.bus._persistence.engine import _seed_default_root

        _seed_default_root(engine)
    return engine


def get_magis_session() -> Generator[Session, None, None]:
    """FastAPI dependency for organisation/control-plane routes."""
    global _session_factory
    engine = get_magis_engine()
    if _url() is not None and _session_factory is None:
        _session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    if _url() is None:
        from magi.bus._persistence.engine import get_session

        yield from get_session()
        return
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def open_magis_session() -> Generator[Session, None, None]:
    """Context manager counterpart for prompt and provider resolution."""
    global _session_factory
    engine = get_magis_engine()
    if _url() is None:
        from magi.bus._persistence.engine import open_session

        with open_session() as session:
            yield session
        return
    if _session_factory is None:
        _session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
