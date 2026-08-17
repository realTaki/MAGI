"""One-time ALTER TABLE pass for legacy sqlite schemas.

Several MAGIS-local tables pre-date :class:`BaseRecordMixin` and
therefore lack ``id`` / ``created_at`` columns even though the ORM
mapper declares them.  SQLAlchemy's ``select(_Row)`` always pulls
every mapped column, so the schema/ORM drift surfaces as
``sqlite3.OperationalError: no such column: X.created_at`` the
first time the runtime touches those books (tool_definitions,
chat_messages, contact_notes, task_runs, …).

The fix is a single ``ALTER TABLE … ADD COLUMN`` per missing
column with a sentinel default value.  ``created_at`` accepts
``'1970-01-01 00:00:00'`` — old rows simply become "as old as
the epoch", which is fine for every consumer that already had to
fall back to ``updated_at`` when ``created_at`` was NULL.

The pass is idempotent: ``PRAGMA table_info`` is read first and
the ``ALTER`` only runs for columns that are still missing.  It
runs at most once per database lifetime — the ``synchronise_schema``
barrier after this helper short-circuits on subsequent boots
because nothing else in the schema changed.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect

logger = logging.getLogger("magi.bus.schema_drift_fix")


# Tables whose legacy schemas lack ``id`` / ``created_at`` even
# though the ORM mapper (BaseRecordMixin) declares them.  Keyed by
# table name; values list the missing columns and the SQL literal
# to use as ``DEFAULT``.  ``id`` is intentionally NOT listed here —
# ``ALTER TABLE … ADD COLUMN id INTEGER PRIMARY KEY`` is not
# supported on a populated SQLite table, and the migrations that
# introduced ``runtime_id`` as the canonical PK (e.g.
# ``0001_init_runtime_state``) pre-date the mixin so the table
# never needs a synthetic ``id``.
_LEGACY_MISSING_COLUMNS: dict[str, dict[str, str]] = {
    # ``created_at`` defaults to the SQLite epoch so the column can be
    # ``NOT NULL`` from the moment it's added — SQLite refuses
    # ``CURRENT_TIMESTAMP`` as an ``ADD COLUMN`` default because it's a
    # non-constant expression.  Existing rows get the sentinel; new
    # rows overwrite it on insert (the ORM mapper supplies the
    # real timestamp via ``BaseRecordMixin.created_at``).
    "chat_conversations": {"created_at": "'1970-01-01 00:00:00'"},
    "tool_definitions": {"created_at": "'1970-01-01 00:00:00'"},
    "tool_catalog_state": {"created_at": "'1970-01-01 00:00:00'"},
    "contact_notes": {"created_at": "'1970-01-01 00:00:00'"},
    "chat_messages": {"created_at": "'1970-01-01 00:00:00'"},
    "task_runs": {"created_at": "'1970-01-01 00:00:00'"},
    # The runtime_state row carries an explicit FK back to the MAGIS
    # memberships row that owns the EVA's identity.  The
    # ``runtime_id`` PK remains canonical, but the ORM mapper
    # declares the FK as a separate column; pre-existing stores
    # migrated from earlier versions need the column added.
    # ``runtime_id`` doubles as the FK value during this one-time
    # backfill so every existing row gets a valid membership_row_id.
    "runtime_state": {"membership_row_id": "runtime_id"},
}


def _table_exists(engine, table: str) -> bool:
    """Return True if ``table`` is a real physical table on this connection.

    SQLAlchemy's :func:`inspect` defaults to the ORM ``MetaData``
    registry, which is empty in our deployment (tables are created
    by :func:`magi.bus.firmwares.schema.synchronise_schema` without ever
    being registered on ``Base.metadata``).  ``inspector.has_table``
    would always return False; ``get_table_names()`` queries
    ``sqlite_master`` directly instead.
    """
    inspector = inspect(engine)
    try:
        return table in inspector.get_table_names()
    except Exception:  # noqa: BLE001
        return False


def _column_exists(engine, table: str, column: str) -> bool:
    """Return True if ``table`` already has ``column`` in its physical schema."""
    inspector = inspect(engine)
    try:
        cols = inspector.get_columns(table)
    except Exception:  # noqa: BLE001
        return False
    return any(c["name"] == column for c in cols)


def apply_schema_drift_fixes(engine) -> int:
    """Patch every legacy table to add the columns its ORM mapper declares.

    Returns the number of ``ALTER TABLE`` statements issued.  Safe to
    call repeatedly — every ALTER is gated on a ``PRAGMA table_info``
    check first.  Errors from a single table do not abort the rest
    of the pass; the runtime can still come up if e.g. one table
    is locked by another connection.

    Implementation note: we open a fresh connection from the pool
    rather than going through ``engine.begin()`` because the same
    pool is already serving the schema barrier and book writes;
    SQLite's single-writer lock would deadlock with a held
    ``BEGIN IMMEDIATE`` from another connection.  Each
    ``ALTER TABLE`` runs in its own implicit transaction.
    """
    if engine is None:
        return 0
    issued = 0
    for table, missing in _LEGACY_MISSING_COLUMNS.items():
        if not _table_exists(engine, table):
            # Table missing entirely — leave schema creation
            # to :func:`magi.bus.firmwares.schema.synchronise_schema`.
            continue
        for column, default_sql in missing.items():
            if _column_exists(engine, table, column):
                continue
            stmt = (
                f'ALTER TABLE "{table}" ADD COLUMN "{column}" '
                f"DATETIME NOT NULL DEFAULT {default_sql}"
            )
            try:
                with engine.connect() as conn:
                    conn.exec_driver_sql(stmt)
                    conn.commit()
                issued += 1
                logger.info(
                    "schema drift fix: added %s.%s (default=%s)",
                    table,
                    column,
                    default_sql,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "schema drift fix: failed to add %s.%s",
                    table,
                    column,
                    exc_info=True,
                )
    return issued


__all__ = ["apply_schema_drift_fixes"]
