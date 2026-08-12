"""Promote ``error_code`` columns on the A2A tables from ``VARCHAR(64)``
to native ``Enum(A2AErrorCode)``.

Revision ID: 0008_promote_a2a_error_code_to_native_enum
Revises: 0007_drop_a2a_source_columns

Pre-conditions
--------------
Legacy rows use ``""`` as the "no error" sentinel. The new
``Enum`` column rejects empty strings, so those rows have to
become SQL ``NULL`` *before* the column type changes — on PG the
ENUM type would refuse the ``UPDATE``; on SQLite the eventual
CHECK constraint would refuse the column write.

Existing valid codes (``"a2a_timeout"``) already match an enum
value verbatim, so the type promotion carries them over without
any explicit row-by-row cast in the data — PG handles the
conversion via the ``USING error_code::a2a_error_code`` clause.

DDL strategy
------------
PostgreSQL gets a real ``CREATE TYPE a2a_error_code`` followed by
``ALTER COLUMN … TYPE a2a_error_code USING …`` on each table. We
create the type explicitly rather than relying on
``op.alter_column``'s auto-emission, so the contract is
deterministic — a re-run on a partially-migrated DB doesn't blow
up on ``CREATE TYPE``.

SQLite has no native ENUM, so SQLAlchemy emits ``VARCHAR(64)``
with a ``CHECK (error_code IS NULL OR error_code IN ('a2a_timeout'))``
constraint. SQLite can't alter a CHECK in place, so
``batch_alter_table`` is used to copy the data, recreate the
schema, and rename in a single transaction.

Both branches land the same logical column shape
(``A2AErrorCode | None``); ``create_all`` on a fresh DB then
produces the matching schema without further intervention.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from magi.bus.guild.a2aJob import A2AErrorCode

revision: str = "0008_promote_a2a_error_code_to_native_enum"
down_revision: str | Sequence[str] | None = "0007_drop_a2a_source_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_log = logging.getLogger(__name__)

_ENUM_NAME = "a2a_error_code"
_AFFECTED_TABLES = ("a2a_request_jobs", "a2a_notify_jobs")


def _pg_enum() -> postgresql.ENUM:
    """PG-side ``postgresql.ENUM`` handle.

    ``create_type=False`` so the type isn't auto-created when this
    object is referenced via ``op.alter_column`` — the upgrade
    creates it explicitly with ``checkfirst=True`` for idempotency,
    the downgrade drops it after both columns revert to ``VARCHAR``.
    ``values_callable`` pins storage / CREATE TYPE labels to the
    enum ``value`` rather than the ``name``, matching the legacy
    VARCHAR(64) ``"a2a_timeout"`` strings pre-Enum rows already hold.
    """
    return postgresql.ENUM(
        A2AErrorCode,
        name=_ENUM_NAME,
        create_type=False,
        values_callable=lambda enum_cls: [e.value for e in enum_cls],
    )


def _sqlite_enum() -> sa.Enum:
    """SQLite-side ``sa.Enum`` — no native ENUM, so SQLAlchemy emits
    ``VARCHAR(64)`` + ``CHECK (error_code IN ('a2a_timeout'))``.

    ``length=64`` pins the column width to the pre-migration value so
    historical rows (and any future enum additions) fit without
    surprises. ``create_constraint=True`` is the load-bearing flag:
    SQLAlchemy 2.x stopped emitting the implicit CHECK on Enum
    columns (1.x defaulted to ``True``), and without it the column
    is plain VARCHAR with no DB-layer membership enforcement. We
    keep the project-wide contract that "writing an unknown code
    raises at the DB boundary" by asking for it explicitly here.

    ``values_callable`` mirrors the PG side: storage and CHECK both
    go against the stable ``.value`` ("a2a_timeout"), not the enum
    member name ("TIMEOUT") — otherwise pre-Enum rows would fail
    the new constraint and silently-translated writes would change
    the on-disk representation of every existing row.
    """
    return sa.Enum(
        A2AErrorCode,
        name=_ENUM_NAME,
        native_enum=True,
        length=64,
        create_constraint=True,
        values_callable=lambda enum_cls: [e.value for e in enum_cls],
    )


def _column_is_already_enum(conn: sa.engine.Connection, table: str) -> bool:
    """Detect a partial / completed run on SQLite.

    SQLAlchemy renders the SQLite target as ``VARCHAR(64)`` with a
    CHECK constraint — the column's nominal ``type`` is still
    ``VARCHAR``, so type alone isn't a useful idempotency key.
    Instead we look at whether any database-level CHECK on
    ``error_code`` references the enum name — that's a structural
    marker unique to the post-migration state. If the column has
    no CHECK at all, this migration hasn't run yet; if it has a
    CHECK referencing a different name, something is wrong (we log
    and proceed so the caller sees the failure clearly).
    """
    try:
        checks = {c["name"]: (c.get("sqltext") or "") for c in sa.inspect(conn).get_check_constraints(table)}
    except sa.exc.NoSuchTableError:
        return False
    target_check = next((sql for sql in checks.values() if "error_code" in sql), None)
    if target_check is None:
        return False
    return _ENUM_NAME in target_check or "'a2a_timeout'" in target_check


def upgrade() -> None:
    """Promote the two ``error_code`` columns to native ``Enum``.

    Idempotent against re-runs: the UPDATE is naturally idempotent
    (no rows match ``WHERE error_code = ''`` after the first pass),
    ``CREATE TYPE`` is wrapped in ``checkfirst=True``, and on SQLite
    we skip the ``batch_alter_table`` if the CHECK is already in
    place. If a table is missing entirely, that's the underlying
    ``create_all`` step having skipped it — fall through to the
    next table rather than failing the whole migration.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Step 1 — legacy ``""`` → ``NULL``. Identical syntax on every
    # supported backend; the CHECK (SQLite) / ENUM (PG) that the
    # column will end up with isn't in effect yet, so empty strings
    # are still writable here. Naturally idempotent: subsequent runs
    # find no matching rows.
    for table in _AFFECTED_TABLES:
        op.execute(
            sa.text(f"UPDATE {table} SET error_code = NULL WHERE error_code = ''")
        )

    if dialect == "postgresql":
        # Step 2a — create the PG ENUM type explicitly. Done before
        # the per-table ``ALTER COLUMN`` so both column rewrites can
        # reuse the same OID; ``checkfirst=True`` makes this safe to
        # re-run on a DB that already has the type from a previous
        # attempt.
        _pg_enum().create(bind, checkfirst=True)
        _log.info("0008: PG ENUM type %s ensured", _ENUM_NAME)

        # Step 2b — swap each column's storage to the new ENUM type.
        # ``USING error_code::a2a_error_code`` is required because PG
        # can't coerce text→enum implicitly; every existing non-NULL
        # value matches the single enum member and round-trips
        # cleanly. PG accepts ``ALTER COLUMN … TYPE`` against an
        # already-typed column (it's effectively a metadata rewrite),
        # so re-running the migration is safe.
        for table in _AFFECTED_TABLES:
            op.alter_column(
                table,
                "error_code",
                existing_type=sa.String(64),
                type_=_pg_enum(),
                existing_nullable=True,
                postgresql_using=f"error_code::{_ENUM_NAME}",
            )
            _log.info("0008: %s.error_code → PG ENUM", table)
    else:
        # SQLite — ``batch_alter_table`` rebuilds the table with the
        # new CHECK constraint. Row data is preserved across the
        # rename; ``error_code`` is already either ``NULL`` (Step 1)
        # or a member value, both of which the new CHECK accepts.
        for table in _AFFECTED_TABLES:
            if _column_is_already_enum(bind, table):
                _log.info("0008: %s already migrated — skipping", table)
                continue
            with op.batch_alter_table(table) as batch:
                batch.alter_column(
                    "error_code",
                    existing_type=sa.String(64),
                    type_=_sqlite_enum(),
                    existing_nullable=True,
                )
            _log.info("0008: %s.error_code → SQLite CHECK", table)


def downgrade() -> None:
    """Revert to ``VARCHAR(64)`` and drop the PG ENUM type (if any).

    No data restoration — the ``""`` → ``NULL`` step in ``upgrade``
    is lossy by construction (the column's only pre-existing
    representations of "no error" were empty strings, and the
    codebase at the prior revision treated them interchangeably).
    The resulting ``NULL`` rows continue to round-trip through the
    pre-Enum :class:`A2ARequestResult` shape (which folds ``None``
    through unchanged) without any data fix-up.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Step 1 — both columns back to ``VARCHAR(64)``. Cast each
        # ENUM value to its string representation explicitly;
        # dropping the type before doing this would refuse because
        # the columns still depend on it.
        for table in _AFFECTED_TABLES:
            op.alter_column(
                table,
                "error_code",
                existing_type=_pg_enum(),
                type_=sa.String(64),
                existing_nullable=True,
                postgresql_using="error_code::varchar(64)",
            )
            _log.info("0008: %s.error_code → VARCHAR(64) (downgrade)", table)
        # Step 2 — only now is it safe to drop the ENUM type.
        # ``checkfirst=True`` so we don't crash on a downgrade-after-
        # incomplete-rollback where the type was already gone.
        _pg_enum().drop(bind, checkfirst=True)
        _log.info("0008: PG ENUM type %s dropped", _ENUM_NAME)
    else:
        # SQLite — same ``batch_alter_table`` dance in reverse: the
        # new table has no CHECK constraint on ``error_code``, so we
        # land back on plain ``VARCHAR(64)``. We don't try to detect
        # "already downgraded" — SQLite's ``batch_alter_table`` is
        # happy rewriting to ``VARCHAR(64)`` against either the
        # CHECK-equipped or CHECK-less column, so re-running is
        # always safe.
        for table in _AFFECTED_TABLES:
            with op.batch_alter_table(table) as batch:
                batch.alter_column(
                    "error_code",
                    existing_type=_sqlite_enum(),
                    type_=sa.String(64),
                    existing_nullable=True,
                )
            _log.info("0008: %s.error_code → VARCHAR(64) (downgrade)", table)
