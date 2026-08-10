"""baseline — stamp the post-create_all schema.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-10 00:00:00

MAGI bootstraps its tables via SQLAlchemy ``Base.metadata.create_all``
in :func:`magi.bus.db.schema.apply_initial_schema` at every boot
(idempotent on already-present tables).  This baseline migration
exists only so the schema-upgrade machinery has a known starting
revision for the post-bootstrap state — ``upgrade`` / ``downgrade``
are no-ops and the DB is expected to already contain every table
that ``Base.metadata`` knows about when :func:`magi.bus.db.schema.upgrade_schema`
is invoked.

Workflow:

- **Fresh DB**: ``apply_initial_schema`` runs ``create_all`` first
  (so the tables exist), then ``upgrade_schema`` lands on
  ``0001_baseline`` and stamps it.  Subsequent migrations
  (``0002``, ``0003``, ...) modify the schema in place.
- **Existing DB from before migrations were introduced**: the first
  boot that runs ``upgrade_schema`` will see an empty alembic
  version table; it falls back to stamping the latest revision that
  produces a no-op against the already-present schema, then any
  delta-only migration runs cleanly.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op: ``Base.metadata.create_all`` already materialised the schema."""
    pass


def downgrade() -> None:
    """No-op: this revision has no schema effect to reverse."""
    pass
