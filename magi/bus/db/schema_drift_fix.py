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
    "chat_conversations": {"created_at": "CURRENT_TIMESTAMP"},
    "tool_definitions": {"created_at": "CURRENT_TIMESTAMP"},
    "tool_catalog_state": {"created_at": "CURRENT_TIMESTAMP"},
    "contact_notes": {"created_at": "CURRENT_TIMESTAMP"},
    "chat_messages": {"created_at": "CURRENT_TIMESTAMP"},
    "task_runs": {"created_at": "CURRENT_TIMESTAMP"},
}


def _column_exists(engine, table: str, column: str) -> bool:
    """Return True if ``table`` already has ``column`` in its physical schema."""
    inspector = inspect(engine)
    if not inspector.has_table(table):
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


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
        if not _column_exists(engine, table, "*"):
            # Table missing entirely — leave schema creation
            # to :func:`magi.bus.db.schema.synchronise_schema`.
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