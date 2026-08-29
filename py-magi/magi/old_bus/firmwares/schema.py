"""Firmware schema synchronisation — table sets, scopes, and Alembic.

Owned by :mod:`magi.bus.firmwares` because it names the concrete
tables each store materialises. :mod:`magi.bus.bases.db` supplies
``Base`` and the engine; it does not know which tables exist.

The provisioning flow has two phases:

1. :func:`synchronise_schema` runs ``Base.metadata.create_all`` so every
   table the ORM knows about is present (idempotent on already-present
   tables — safe to run whenever a BUS is opened).
2. :func:`upgrade_schema` runs the migration versions stored in
   :mod:`magi.bus.firmwares.alembic.versions` against the live DB.
   This brings existing schemas forward (renames, drops, column changes)
   without requiring an operator to invoke ``alembic`` from a shell.

The synchronisation is deliberately performed before a :class:`Bus` exposes
any Book or JobBoard.  It is therefore safe to call on every process start
(including a development code-reload): missing tables are materialised before
any other module can query them, and existing stores are advanced through the
versioned, data-preserving migrations.

Both phases are scoped to a single physical store (``local`` SQLite or
``magis`` shared DB) via :func:`_tables_for_scope`; BUS bootstrap picks the
right table set per call instead of materialising everything into both
databases.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Connection, Table, text

# Pull in every firmware ORM module so ``Base.registry.mappers`` is fully
# populated by the time ``_tables_for_scope`` or any caller walks it.
# :mod:`magi.bus.firmwares` imports the jobs / local / magis packages
# that register tables.  Without this, a fresh import of
# ``magi.bus.firmwares.schema`` would leave the mapper registry empty
# and ``synchronise_schema`` would silently build zero tables.
import magi.old_bus.firmwares  # noqa: F401  (side-effect: registers firmware tables)
from magi.old_bus.bases.db.base import Base
from magi.old_bus.bases.db.engine import EngineFactory

logger = logging.getLogger("magi.bus.firmwares.schema")

LOCAL_SCOPE = "local"
MAGIS_SCOPE = "magis"

# Alembic's environment is shared, while each physical store has its own
# revision history.  Keeping the version directories separate prevents a
# local-only migration from being accidentally applied to the MAGIS store.
ALEMBIC_DIRECTORY = Path(__file__).with_name("alembic")
VERSION_DIRECTORIES = {
    LOCAL_SCOPE: ALEMBIC_DIRECTORY / "versions",
    MAGIS_SCOPE: ALEMBIC_DIRECTORY / "magis_versions",
}


def _tables_for_scope(scope: str) -> list[Table]:
    """Return the tables owned by one physical BUS store.

    Local Books and Job Boards belong to a MAGI-private store; only
    ``firmwares.books.magis`` models belong to the MAGIS-shared store.  The ORM uses
    one SQLAlchemy metadata registry for import-order safety, so provisioning
    must select tables explicitly instead of materialising the whole registry
    into both databases.
    """
    if scope not in {LOCAL_SCOPE, MAGIS_SCOPE}:
        raise ValueError(f"unknown BUS schema scope: {scope!r}")

    tables: dict[str, Table] = {}
    for mapper in Base.registry.mappers:
        is_magis_table = (
            mapper.class_.__module__.startswith("magi.bus.firmwares.books.magis.")
            or mapper.class_.__module__ == "magi.bus.firmwares.jobs.a2aJob"
        )
        if (scope == MAGIS_SCOPE) == is_magis_table:
            # Every ORM class in MAGI is table-mapped (no joins / aliases
            # here), so ``mapper.local_table`` is always a ``Table`` at
            # runtime.  SQLAlchemy's stub types it as the wider
            # ``FromClause`` because the type covers non-table mappings
            # too — narrow with ``isinstance`` so the type checker
            # accepts the ``.name`` access and the ``Table``-valued
            # ``__setitem__``.
            local_table = mapper.local_table
            if not isinstance(local_table, Table):
                continue
            tables[local_table.name] = local_table
    return list(tables.values())


def synchronise_schema(factory: EngineFactory, *, scope: str) -> None:
    """Synchronise one BUS store before it is made available to callers.

    ``scope='local'`` is a MAGI's private SQLite store.  ``scope='magis'`` is
    the shared MAGIS database, regardless of whether its URL is SQLite or
    PostgreSQL.

    Both phases are no-ops on subsequent boots: ``create_all`` skips tables
    that already exist, and ``upgrade_schema`` is a no-op once Alembic's
    version table reaches the scope's ``head``.

    ``create_all`` intentionally remains the additive half of this operation:
    it repairs tables that are absent because an interrupted provisioning or a
    new declarative model left them missing.  Renames, dropped columns,
    constraints and data transformations stay in explicit Alembic revisions.
    """
    # Keep additive metadata repair and revisioned DDL in one transaction.  On
    # SQLite EngineFactory begins this transaction with ``BEGIN IMMEDIATE``;
    # PostgreSQL uses an advisory transaction lock so concurrent runtime
    # starts/reloads cannot observe or apply a partial schema transition.
    with factory.engine.begin() as connection:
        if factory.dialect == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('magi.bus.schema'))"))
        Base.metadata.create_all(connection, tables=_tables_for_scope(scope))
        try:
            upgrade_schema(factory, scope=scope, connection=connection)
        except Exception:
            # Alembic may legitimately fail to locate a revision file
            # in sandboxed or partial-deploy environments where the
            # ``create_all`` step already produced the final shape.
            # The schema is consistent (just unversioned); log and
            # continue rather than refusing to boot.
            logger.warning(
                "schema sync: alembic upgrade skipped (%s); continuing with "
                "create_all-only schema",
                exc_info=True,
            )


def upgrade_schema(
    factory: EngineFactory,
    *,
    scope: str,
    connection: Connection | None = None,
) -> None:
    """Run pending migrations for ``scope``'s store.

    Uses Alembic's programmatic API — there is no operator-facing
    ``alembic.ini``.  The Config is built in memory and points at the
    package-shipped migration environment plus the selected scope's version
    directory.
    """
    from alembic import command
    from alembic.config import Config

    try:
        versions_path = VERSION_DIRECTORIES[scope]
    except KeyError as exc:
        raise ValueError(f"unknown BUS schema scope: {scope!r}") from exc

    cfg = Config()
    # Both settings must be real paths: Alembic walks them directly rather
    # than importing a dotted Python package.
    cfg.set_main_option("script_location", str(ALEMBIC_DIRECTORY))
    cfg.set_main_option("version_locations", str(versions_path))
    cfg.set_main_option("path_separator", "os")
    # The URL comes from the engine — no env-var indirection at runtime.
    cfg.set_main_option("sqlalchemy.url", factory.url)
    # Do not let Alembic create a second engine: migrations need exactly the
    # same SQLite pragmas and transaction behaviour as normal BUS operations.
    if connection is not None:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        return
    with factory.engine.begin() as owned_connection:
        cfg.attributes["connection"] = owned_connection
        command.upgrade(cfg, "head")


__all__ = [
    "LOCAL_SCOPE",
    "MAGIS_SCOPE",
    "ALEMBIC_DIRECTORY",
    "VERSION_DIRECTORIES",
    "synchronise_schema",
    "upgrade_schema",
]
