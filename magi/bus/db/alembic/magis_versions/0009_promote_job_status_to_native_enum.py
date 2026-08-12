"""Promote ``status`` columns on the A2A Job boards from
``VARCHAR(24)`` to native ``Enum(JobStatus)``.

Revision ID: 0009_promote_job_status_to_native_enum
Revises: 0008_promote_a2a_error_code_to_native_enum

The A2A tables (``a2a_request_jobs`` / ``a2a_notify_jobs``) live on
the MAGIS branch but inherit :class:`JobRowMixin` like the rest of
the Job boards, so they need the same column promotion. The main
branch's :mod:`magi.bus.db.alembic.versions.0019_promote_job_status_to_native_enum`
covers the other eight tables; this migration handles the two that
are MAGIS-only.

:data:`JobStatus` has exactly four members whose ``.value`` strings
(``"pending"`` / ``"processing"`` / ``"completed"`` / ``"failed"``)
match the pre-migration column contents verbatim — no data
rewrite is needed, only the column type changes. DDL strategy
mirrors the main-branch migration and
:mod:`magi.bus.db.alembic.magis_versions.0008_promote_a2a_error_code_to_native_enum`:

- PostgreSQL gets a ``CREATE TYPE job_status`` + per-table
  ``ALTER COLUMN … TYPE job_status USING status::job_status``.
- SQLite has no native ENUM, so SQLAlchemy emits ``VARCHAR(24)``
  with a ``CHECK (status IN (...))`` constraint via
  ``batch_alter_table``.

Both branches land the same logical column shape (``JobStatus``);
``create_all`` on a fresh DB produces the matching schema without
further intervention.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from magi.bus.guild.base import JobStatus

revision: str = "0009_promote_job_status_to_native_enum"
down_revision: str | Sequence[str] | None = "0008_promote_a2a_error_code_to_native_enum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_log = logging.getLogger(__name__)

_ENUM_NAME = "job_status"
#: A2A tables that share :class:`JobRowMixin` on the MAGIS branch.
_AFFECTED_TABLES: tuple[str, ...] = (
    "a2a_request_jobs",
    "a2a_notify_jobs",
)


def _pg_enum() -> postgresql.ENUM:
    """PG-side ``postgresql.ENUM`` handle — see main branch."""
    return postgresql.ENUM(
        JobStatus,
        name=_ENUM_NAME,
        create_type=False,
        values_callable=lambda enum_cls: [e.value for e in enum_cls],
    )


def _sqlite_enum() -> sa.Enum:
    """SQLite-side ``sa.Enum`` — see main branch."""
    return sa.Enum(
        JobStatus,
        name=_ENUM_NAME,
        native_enum=True,
        length=24,
        create_constraint=True,
        values_callable=lambda enum_cls: [e.value for e in enum_cls],
    )


def _column_is_already_enum(conn: sa.engine.Connection, table: str) -> bool:
    """Detect a partial / completed run on SQLite."""
    try:
        checks = {c["name"]: (c.get("sqltext") or "") for c in sa.inspect(conn).get_check_constraints(table)}
    except sa.exc.NoSuchTableError:
        return False
    target_check = next((sql for sql in checks.values() if "status" in sql), None)
    if target_check is None:
        return False
    return _ENUM_NAME in target_check


def upgrade() -> None:
    """Promote each ``status`` column to native ``Enum``."""
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        _pg_enum().create(bind, checkfirst=True)
        _log.info("0009: PG ENUM type %s ensured", _ENUM_NAME)

        for table in _AFFECTED_TABLES:
            op.alter_column(
                table,
                "status",
                existing_type=sa.String(24),
                type_=_pg_enum(),
                existing_nullable=False,
                postgresql_using=f"status::{_ENUM_NAME}",
            )
            _log.info("0009: %s.status → PG ENUM", table)
    else:
        for table in _AFFECTED_TABLES:
            if _column_is_already_enum(bind, table):
                _log.info("0009: %s already migrated — skipping", table)
                continue
            with op.batch_alter_table(table) as batch:
                batch.alter_column(
                    "status",
                    existing_type=sa.String(24),
                    type_=_sqlite_enum(),
                    existing_nullable=False,
                )
            _log.info("0009: %s.status → SQLite CHECK", table)


def downgrade() -> None:
    """Revert to ``VARCHAR(24)`` and drop the PG ENUM type (if any).

    No data restoration needed — every pre-migration value is an
    enum member, and ``VARCHAR(24)`` round-trips the same string.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        for table in _AFFECTED_TABLES:
            op.alter_column(
                table,
                "status",
                existing_type=_pg_enum(),
                type_=sa.String(24),
                existing_nullable=False,
                postgresql_using="status::varchar(24)",
            )
            _log.info("0009: %s.status → VARCHAR(24) (downgrade)", table)
        _pg_enum().drop(bind, checkfirst=True)
        _log.info("0009: PG ENUM type %s dropped", _ENUM_NAME)
    else:
        for table in _AFFECTED_TABLES:
            with op.batch_alter_table(table) as batch:
                batch.alter_column(
                    "status",
                    existing_type=_sqlite_enum(),
                    type_=sa.String(24),
                    existing_nullable=False,
                )
            _log.info("0009: %s.status → VARCHAR(24) (downgrade)", table)
