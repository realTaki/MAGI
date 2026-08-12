"""Promote ``status`` columns on every Job board from ``VARCHAR(24)``
to native ``Enum(JobStatus)``.

Revision ID: 0019_promote_job_status_to_native_enum
Revises: 0018_drop_tool_jobs_source

Eight ``_XxxJobRow`` tables share :class:`JobRowMixin` and therefore
share one ``status VARCHAR(24)`` column declaration. The new
:class:`JobStatus` enum has exactly four members whose ``.value``
strings (``"pending"`` / ``"processing"`` / ``"completed"`` /
``"failed"``) match the pre-migration column contents verbatim — so
no data rewrite is needed; only the column type changes.

DDL strategy mirrors :mod:`magi.bus.db.alembic.magis_versions.0008_promote_a2a_error_code_to_native_enum`:

- PostgreSQL gets a ``CREATE TYPE job_status`` + per-table
  ``ALTER COLUMN … TYPE job_status USING status::job_status``.
- SQLite has no native ENUM, so SQLAlchemy emits ``VARCHAR(24)``
  with a ``CHECK (status IN (...))`` constraint via
  ``batch_alter_table`` (which copies data, recreates the schema,
  and renames in one transaction).

Both branches land the same logical column shape (``JobStatus``);
``create_all`` on a fresh DB produces the matching schema without
further intervention.

Pre-conditions
--------------
Every existing row already holds one of the four enum members (the
mixin defaults to ``JobStatus.PENDING`` and ``BaseJobBoard`` only
writes the same four values), so the type promotion carries them
over without any explicit row-by-row cast.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from magi.bus.guild.base import JobStatus

revision: str = "0019_promote_job_status_to_native_enum"
down_revision: str | Sequence[str] | None = "0018_drop_tool_jobs_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_log = logging.getLogger(__name__)

_ENUM_NAME = "job_status"
#: Job tables that share :class:`JobRowMixin` (the A2A tables live on the
#: MAGIS branch and are handled by ``magis_versions/0009_…``).
_AFFECTED_TABLES: tuple[str, ...] = (
    "chat_jobs",
    "delivery_outbox",
    "tool_jobs",
    "llm_jobs",
    "run_task_jobs",
    "change_provider_config_jobs",
    "mcp_server_changed_jobs",
    "seed_preset_tasks_jobs",
)


def _pg_enum() -> postgresql.ENUM:
    """PG-side ``postgresql.ENUM`` handle.

    ``create_type=False`` so the type isn't auto-created when this
    object is referenced via ``op.alter_column`` — the upgrade
    creates it explicitly with ``checkfirst=True`` for idempotency,
    the downgrade drops it after every column reverts to VARCHAR.
    ``values_callable`` pins storage / CREATE TYPE labels to the
    enum ``.value`` ("pending" / "processing" / "completed" /
    "failed"), matching the legacy VARCHAR contents verbatim.
    """
    return postgresql.ENUM(
        JobStatus,
        name=_ENUM_NAME,
        create_type=False,
        values_callable=lambda enum_cls: [e.value for e in enum_cls],
    )


def _sqlite_enum() -> sa.Enum:
    """SQLite-side ``sa.Enum`` — no native ENUM, so SQLAlchemy emits
    ``VARCHAR(24)`` + ``CHECK (status IN (...))``.

    ``length=24`` pins the column width to the pre-migration value
    so historical rows (and any future enum additions) fit without
    surprises. ``create_constraint=True`` is the load-bearing flag:
    SQLAlchemy 2.x stopped emitting the implicit CHECK on Enum
    columns (1.x defaulted to ``True``), and without it the column
    is plain VARCHAR with no DB-layer membership enforcement. We
    keep the project-wide contract that "writing an unknown status
    raises at the DB boundary" by asking for it explicitly here.
    """
    return sa.Enum(
        JobStatus,
        name=_ENUM_NAME,
        native_enum=True,
        length=24,
        create_constraint=True,
        values_callable=lambda enum_cls: [e.value for e in enum_cls],
    )


def _column_is_already_enum(conn: sa.engine.Connection, table: str) -> bool:
    """Detect a partial / completed run on SQLite.

    SQLAlchemy renders the SQLite target as ``VARCHAR(24)`` with a
    CHECK constraint — the column's nominal ``type`` is still
    ``VARCHAR``, so type alone isn't a useful idempotency key.
    Instead we look at whether any database-level CHECK on ``status``
    references the enum name — that's a structural marker unique to
    the post-migration state.
    """
    try:
        checks = {c["name"]: (c.get("sqltext") or "") for c in sa.inspect(conn).get_check_constraints(table)}
    except sa.exc.NoSuchTableError:
        return False
    target_check = next((sql for sql in checks.values() if "status" in sql), None)
    if target_check is None:
        return False
    return _ENUM_NAME in target_check


def upgrade() -> None:
    """Promote each ``status`` column to native ``Enum``.

    Idempotent against re-runs: ``CREATE TYPE`` is wrapped in
    ``checkfirst=True``, and on SQLite we skip ``batch_alter_table``
    if the CHECK is already in place. If a table is missing
    entirely, fall through to the next table rather than failing
    the whole migration.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Create the PG ENUM type explicitly. Done before per-table
        # ``ALTER COLUMN`` so every column rewrite reuses the same
        # OID; ``checkfirst=True`` makes this safe to re-run on a DB
        # that already has the type from a previous attempt.
        _pg_enum().create(bind, checkfirst=True)
        _log.info("0019: PG ENUM type %s ensured", _ENUM_NAME)

        # Swap each column's storage to the new ENUM type.
        # ``USING status::job_status`` is required because PG can't
        # coerce text→enum implicitly; every existing non-NULL value
        # matches an enum member and round-trips cleanly. PG accepts
        # ``ALTER COLUMN … TYPE`` against an already-typed column
        # (it's effectively a metadata rewrite), so re-running is
        # safe.
        for table in _AFFECTED_TABLES:
            op.alter_column(
                table,
                "status",
                existing_type=sa.String(24),
                type_=_pg_enum(),
                existing_nullable=False,
                postgresql_using=f"status::{_ENUM_NAME}",
            )
            _log.info("0019: %s.status → PG ENUM", table)
    else:
        # SQLite — ``batch_alter_table`` rebuilds the table with the
        # new CHECK constraint. Row data is preserved across the
        # rename; every existing ``status`` already matches an enum
        # member, so the new CHECK accepts it as-is.
        for table in _AFFECTED_TABLES:
            if _column_is_already_enum(bind, table):
                _log.info("0019: %s already migrated — skipping", table)
                continue
            with op.batch_alter_table(table) as batch:
                batch.alter_column(
                    "status",
                    existing_type=sa.String(24),
                    type_=_sqlite_enum(),
                    existing_nullable=False,
                )
            _log.info("0019: %s.status → SQLite CHECK", table)


def downgrade() -> None:
    """Revert to ``VARCHAR(24)`` and drop the PG ENUM type (if any).

    No data restoration needed — every pre-migration value is an
    enum member, and ``VARCHAR(24)`` round-trips the same string.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Both columns back to ``VARCHAR(24)``. Cast each ENUM value
        # to its string representation explicitly; dropping the
        # type before doing this would refuse because the columns
        # still depend on it.
        for table in _AFFECTED_TABLES:
            op.alter_column(
                table,
                "status",
                existing_type=_pg_enum(),
                type_=sa.String(24),
                existing_nullable=False,
                postgresql_using="status::varchar(24)",
            )
            _log.info("0019: %s.status → VARCHAR(24) (downgrade)", table)
        # Only now is it safe to drop the ENUM type.
        # ``checkfirst=True`` so we don't crash on a downgrade-after-
        # incomplete-rollback where the type was already gone.
        _pg_enum().drop(bind, checkfirst=True)
        _log.info("0019: PG ENUM type %s dropped", _ENUM_NAME)
    else:
        # SQLite — same ``batch_alter_table`` dance in reverse: the
        # new table has no CHECK constraint on ``status``, so we
        # land back on plain ``VARCHAR(24)``. Re-running is always
        # safe because SQLite is happy rewriting to ``VARCHAR(24)``
        # against either the CHECK-equipped or CHECK-less column.
        for table in _AFFECTED_TABLES:
            with op.batch_alter_table(table) as batch:
                batch.alter_column(
                    "status",
                    existing_type=_sqlite_enum(),
                    type_=sa.String(24),
                    existing_nullable=False,
                )
            _log.info("0019: %s.status → VARCHAR(24) (downgrade)", table)
