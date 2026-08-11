"""rename contacts.telegram_id → contacts.tgid.

Revision ID: 0006_rename_contacts_telegram_id_to_tgid
Revises: 0005_drop_legacy_local_a2a_jobs
Create Date: 2026-08-11 00:00:00

Companion to the ``telegram_id`` → ``tgid`` rename in
:mod:`magi.bus.library.local.contactBook`.  ``tgid`` is the canonical
name for a Telegram user in ``docs/terms.md``; this brings the column
in line with the ORM attribute, the DTO field, the API payload key and
the documentation, so the one concept has exactly one spelling.

Idempotency
===========

The rename is guarded by an inspection of the live schema — re-running
against an already-current DB is a no-op.  This matters because
:func:`magi.bus.db.schema.upgrade_schema` runs before every BUS is
opened, and :func:`synchronise_schema` runs ``create_all`` first (which
on a fresh DB already produces the post-rename shape, i.e. ``tgid``).
Without the guard, a fresh DB would hit ``no such column: telegram_id``
on the very first boot.

Notes
=====

- ``ALTER TABLE ... RENAME COLUMN`` automatically follows indexes on the
  renamed column, so no explicit index rebuild is needed.
- ``contacts`` is a MAGI-private (``local`` scope) table, so this
  revision belongs to ``versions/`` rather than ``magis_versions/``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_rename_contacts_telegram_id_to_tgid"
down_revision: str | Sequence[str] | None = "0005_drop_legacy_local_a2a_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(conn: sa.engine.Connection, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()

    # Only rename if the old name is still present — fresh DBs already
    # have ``tgid`` from ``create_all``.
    if _has_column(conn, "contacts", "telegram_id") and not _has_column(conn, "contacts", "tgid"):
        op.execute("ALTER TABLE contacts RENAME COLUMN telegram_id TO tgid")


def downgrade() -> None:
    """Reverse: rename ``tgid`` back to ``telegram_id`` (guarded)."""
    conn = op.get_bind()

    if _has_column(conn, "contacts", "tgid") and not _has_column(conn, "contacts", "telegram_id"):
        op.execute("ALTER TABLE contacts RENAME COLUMN tgid TO telegram_id")
