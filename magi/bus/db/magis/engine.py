
"""Public database access for a MAGIS.

Each MAGI keeps a private SQLite database for its memory, sessions and local
work.  Organisation facts live in the PostgreSQL database of its direct
MAGIS.  The sole runtime discovery input is ``MAGIS_DATABASE_URL``; Kubernetes
mounts it from the MAGIS database Secret, rather than passing provider or
instruction values as environment variables.

Phase 3 — the Composition Root may inject an explicit MAGIS engine
(``set_injected_magis_engine``) so the Local Profile can back the
public schema with a dedicated SQLite file.  Production code raises
when neither an injection nor a PostgreSQL DSN is present.
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
_injected_magis_engine: Engine | None = None


def _url() -> str | None:
    return os.environ.get("MAGIS_DATABASE_URL") or None


def set_injected_magis_engine(engine: Engine | None) -> None:
    """Store the Composition-Root-injected MAGIS engine.

    Called by :func:`magi.bus.bootstrap` when the caller supplies a
    ``magis_engine`` keyword.  Pass ``None`` to clear.  The injected
    engine is preferred over the env-var lookup so the Local Profile
    can supply a per-MAGIS SQLite file regardless of
    ``MAGIS_DATABASE_URL``.
    """
    global _injected_magis_engine
    _injected_magis_engine = engine


def get_magis_engine() -> Engine:
    """Return this node's direct MAGIS public database engine.

    Order of resolution (Phase 3):

    1. The Composition-Root-injected engine (Local Profile per-MAGIS SQLite).
    2. ``MAGIS_DATABASE_URL`` for the K8s Profile (PostgreSQL).

    Raises when neither is configured (per plan §6.1).
    """
    global _engine, _session_factory
    if _injected_magis_engine is not None:
        return _injected_magis_engine
    url = _url()
    if url is None:
        raise RuntimeError(
            "MAGIS database is not configured. Set MAGIS_DATABASE_URL (K8s Profile) "
            "or inject a magis_engine via magi.bus.bootstrap(...)."
        )
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
    if _url() is not None or _injected_magis_engine is not None:
        # SQLAlchemy sorts the FK dependencies (magic before magis) for us.
        MAGIS.metadata.create_all(
            engine,
            tables=[MAGIC.__table__, MAGIS.__table__, MAGISRole.__table__, MAGISMembership.__table__, MAGISAdmin.__table__, EveRuntime.__table__, ControlSetting.__table__, ControlOperator.__table__],
        )
    if seed_root:
        from magi.bus.db.engine import _seed_default_root

        _seed_default_root(engine)
    return engine


def get_magis_session() -> Generator[Session, None, None]:
    """FastAPI dependency for organisation/control-plane routes."""
    global _session_factory
    engine = get_magis_engine()
    if (_url() is not None or _injected_magis_engine is not None) and _session_factory is None:
        _session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
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
    if _session_factory is None:
        _session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
