"""Single-shot initial schema for the MAGIS-shared store.

Revision ID: 0001_initial_schema

This is the **only** alembic revision for the magis scope. The
2026.08 dev-mode collapse folded the previous 9-revision chain
(0001-0009) into one baseline so every dev install starts with the
final schema and Alembic's ``upgrade head`` is a single transaction.

Schema source of truth is the declarative ORM in
:mod:`magi.bus.firmwares.books.magis` and ``magi.bus.firmwares.jobs.a2aJob`` (the
two ``JobRowMixin``-derived tables whose module path is rooted at
``magi.bus.firmwares.jobs`` are intentionally grouped with the MAGIS
store — see :func:`magi.bus.firmwares.schema._tables_for_scope`). This
migration's :func:`upgrade` simply hands the scope-filtered table
list to :meth:`sqlalchemy.sql.schema.MetaData.create_all`, which
emits the matching ``CREATE TABLE`` / ``CREATE INDEX`` /
``CREATE UNIQUE INDEX`` statements for every model.

Dev environment only — we don't carry the historical revision
chain forward because there is no operator deployment to migrate.
See :mod:`magi.bus.firmwares.alembic.versions.0001_initial_schema` for
the matching baseline on the local BUS store.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the complete MAGIS-store schema from the current ORM.

    Idempotent against partial state: ``create_all`` emits ``IF NOT
    EXISTS`` style guards for tables / indexes and silently skips
    anything already on disk. A fresh DB lands at this revision in
    one transaction.
    """
    from magi.bus.bases.db.base import Base
    from magi.bus.firmwares.schema import MAGIS_SCOPE, _tables_for_scope

    Base.metadata.create_all(
        op.get_bind(),
        tables=_tables_for_scope(MAGIS_SCOPE),
    )


def downgrade() -> None:
    """Drop every MAGIS-store table.

    Dev environment only — no destructive-downgrade contract. The
    reverse walk orders children before parents so existing FKs
    don't block the drop.
    """
    from magi.bus.firmwares.schema import MAGIS_SCOPE, _tables_for_scope

    bind = op.get_bind()
    for table in reversed(_tables_for_scope(MAGIS_SCOPE)):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
