"""Internal schema revision materialisation owned by BUS provisioning.

The provisioning flow has two phases:

1. :func:`apply_initial_schema` runs ``Base.metadata.create_all`` so every
   table the ORM knows about is present (idempotent on already-present
   tables — safe to run on every boot).
2. :func:`upgrade_schema` runs the migration versions stored in
   :mod:`magi.bus.db.schema_migrations.versions` against the live DB.
   This brings existing schemas forward (renames, drops, column changes)
   without requiring an operator to invoke ``alembic`` from a shell.

Both phases are scoped to a single physical store (``local`` SQLite or
``magis`` shared DB) via :func:`_tables_for_scope`; provisioning picks
the right table set per call instead of materialising everything into
both databases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Table

from magi.bus.db.base import Base
from magi.bus.db.engine import EngineFactory

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


LOCAL_SCOPE = "local"
MAGIS_SCOPE = "magis"

# Dotted module path to the migration versions directory.
# ``upgrade_schema`` points alembic at this via ``Config(script_location=...)``
# so the migration files ship with the package (k8s image build picks up
# ``magi/`` via ``[tool.uv.build-backend]`` in ``pyproject.toml``).
VERSIONS_PACKAGE = "magi.bus.db.alembic"


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
    """Materialise the requested store's schema and run pending migrations.

    ``scope='local'`` is a MAGI's private SQLite store.  ``scope='magis'`` is
    the shared MAGIS database, regardless of whether its URL is SQLite or
    PostgreSQL.

    Both phases are no-ops on subsequent boots: ``create_all`` skips tables
    that already exist, and ``upgrade_schema`` is a no-op once the version
    table reaches ``head``.
    """
    Base.metadata.create_all(factory.engine, tables=_tables_for_scope(scope))
    upgrade_schema(factory, scope=scope)


def upgrade_schema(factory: EngineFactory, *, scope: str) -> None:
    """Run pending migrations for ``scope``'s store.

    Uses alembic's programmatic API — there is no ``alembic.ini`` and
    no operator-facing CLI.  ``Config`` is built in memory and pointed at
    :data:`VERSIONS_PACKAGE`, the version files live entirely inside the
    ``magi`` package.

    Scope handling: the ``local`` scope runs every migration in the
    versions directory; the ``magis`` scope currently has no migrations
    (its tables don't intersect with the run_id / event_id renames), so
    it skips the upgrade entirely.  When magis gets its first migration,
    it goes into a parallel sub-package (e.g.
    ``magi.bus.db.schema_migrations.magis_versions``) and gets picked up
    here alongside ``local``.
    """
    if scope == MAGIS_SCOPE:
        # No magis-scoped migrations yet; the shared store is created by
        # ``create_all`` alone.  When the first magis migration lands,
        # dispatch to its own versions directory here.
        return

    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", VERSIONS_PACKAGE)
    # The URL comes from the engine — no env-var indirection at runtime.
    cfg.set_main_option("sqlalchemy.url", factory.url)
    cfg.attributes["target_metadata"] = Base.metadata

    # Reuse the engine's connection so pragmas (WAL, foreign_keys,
    # busy_timeout, BEGIN IMMEDIATE) stay consistent with normal
    # application traffic.
    with factory.engine.connect() as connection:
        _run_alembic_upgrade(cfg, connection)
        connection.commit()


def _run_alembic_upgrade(cfg: "Config", connection: "Connection") -> None:
    """Bind the alembic ``Context`` to an existing connection and run.

    Split out so :func:`upgrade_schema` keeps the scope-dispatch +
    engine-borrow logic linear.
    """
    from alembic.runtime.environments import EnvironmentContext

    script_dir = cfg.get_main_option("script_location")
    with EnvironmentContext(
        cfg,
        script_dir,
        script=cfg.get_section(cfg.config_ini_section)["script_location"],
        target_metadata=cfg.attributes.get("target_metadata"),
    ) as env:
        env.configure(connection=connection, target_metadata=cfg.attributes.get("target_metadata"))
        with env.begin_transaction():
            env.run_migrations()


__all__ = [
    "LOCAL_SCOPE",
    "MAGIS_SCOPE",
    "VERSIONS_PACKAGE",
    "apply_initial_schema",
    "upgrade_schema",
]
