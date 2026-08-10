"""Internal schema revision materialisation owned by BUS provisioning.

The provisioning flow has two phases:

1. :func:`apply_initial_schema` runs ``Base.metadata.create_all`` so every
   table the ORM knows about is present (idempotent on already-present
   tables — safe to run on every boot).
2. :func:`upgrade_schema` runs the migration versions stored in
   :mod:`magi.bus.db.alembic.versions` against the live DB.
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
    ``magi.bus.db.alembic.magis_versions``) and gets picked up
    here alongside ``local``.
    """
    if scope == MAGIS_SCOPE:
        # No magis-scoped migrations yet; the shared store is created by
        # ``create_all`` alone.  When the first magis migration lands,
        # dispatch to its own versions directory here.
        return

    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    # ``script_location`` must be an absolute filesystem path — alembic's
    # ``ScriptDirectory.from_config`` walks the directory directly without
    # going through ``importlib``.  Resolve the dotted module path
    # (:data:`VERSIONS_PACKAGE`) into the package's on-disk location.
    import importlib
    pkg = importlib.import_module(VERSIONS_PACKAGE)
    pkg_path = Path(next(iter(pkg.__path__)))
    cfg = Config()
    cfg.set_main_option("script_location", str(pkg_path))
    # The URL comes from the engine — no env-var indirection at runtime.
    cfg.set_main_option("sqlalchemy.url", factory.url)

    # ``command.upgrade`` opens its own engine internally.  Pragmas
    # (WAL / foreign_keys / busy_timeout / BEGIN IMMEDIATE) are set
    # on the engine factory we already have, so the migration runs
    # against the same SQLite file with the same connection semantics
    # as the rest of the app — no special plumbing needed.
    command.upgrade(cfg, "head")


__all__ = [
    "LOCAL_SCOPE",
    "MAGIS_SCOPE",
    "VERSIONS_PACKAGE",
    "apply_initial_schema",
    "upgrade_schema",
]
