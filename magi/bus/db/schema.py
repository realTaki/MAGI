"""Internal schema revision materialisation owned by BUS provisioning."""

from __future__ import annotations

from sqlalchemy import Table

from magi.bus.db.base import Base
from magi.bus.db.engine import EngineFactory


LOCAL_SCOPE = "local"
MAGIS_SCOPE = "magis"


def _tables_for_scope(scope: str) -> list[Table]:
    """Return the tables owned by one physical BUS store.

    Local Books and Guild job boards belong to a MAGI-private store; only
    ``library.magis`` models belong to the MAGIS-shared store.  The ORM uses
    one SQLAlchemy metadata registry for import-order safety, so provisioning
    must select tables explicitly instead of materialising the whole registry
    into both databases.
    """
    if scope not in {LOCAL_SCOPE, MAGIS_SCOPE}:
        raise ValueError(f"unknown BUS schema scope: {scope!r}")

    tables: dict[str, Table] = {}
    for mapper in Base.registry.mappers:
        is_magis_table = mapper.class_.__module__.startswith("magi.bus.library.magis.")
        if (scope == MAGIS_SCOPE) == is_magis_table:
            tables[mapper.local_table.name] = mapper.local_table
    return list(tables.values())


def apply_initial_schema(factory: EngineFactory, *, scope: str) -> None:
    """Materialise only the requested physical-store schema.

    ``scope='local'`` is a MAGI's private SQLite store.  ``scope='magis'`` is
    the shared MAGIS database, regardless of whether its URL is SQLite or
    PostgreSQL.
    """
    Base.metadata.create_all(factory.engine, tables=_tables_for_scope(scope))


__all__ = ["LOCAL_SCOPE", "MAGIS_SCOPE", "apply_initial_schema"]
